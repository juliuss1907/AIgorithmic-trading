"""Explicit research collection over the existing Jev transport/credentials, no journal writes."""

from dataclasses import dataclass
from decimal import Decimal
from http.client import HTTPException
import json
import math
import socket
import time
import urllib.error

from intraday.jev_review_cache import answer_key, canonical
from intraday.jev_review_budget import response_cost
from intraday.provider_client import http_transport
from intraday.providers import JevDecisionProvider
from intraday.setup2_review import MODEL, TOKEN_GROUPS, parse_answer, pinned_returned_model, questions

WORKFLOW = 'setup2_review'


def request_payload(tokens):
    tokens = list(tokens)
    if len(tokens) != len(TOKEN_GROUPS) or any(t not in group for t, group in zip(tokens, TOKEN_GROUPS.values())):
        raise ValueError('review state must use the fixed anonymous vocabulary')
    return dict(model=MODEL, state={'tokens': tokens}, questions=questions())


@dataclass(frozen=True)
class ReviewCall:
    status: str
    payload: dict | None = None
    retry_after: float = 0
    cost_usd: Decimal | None = None


class ReviewProvider:
    def __init__(self, credential, *, transport=None, clock=time.monotonic, timeout_seconds=5):
        if credential.profile.model != MODEL or credential.profile.role.value != 'jev':
            raise ValueError('review requires the pinned Jev credential profile')
        if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 60:
            raise ValueError('invalid research provider timeout')
        self.credential = credential
        self.transport, self.clock, self.timeout = transport or http_transport, clock, timeout_seconds

    def request(self, tokens):
        body = canonical(request_payload(tokens)).encode()
        started = self.clock()
        try:
            response = self.transport(url=self.credential.profile.base_url,
                headers={'Authorization': f'Bearer {self.credential.api_key}', 'Content-Type': 'application/json',
                         'Accept': 'application/json', 'User-Agent': 'AIgorithmic-Trading/0.1 setup2-review'},
                body=body, timeout=self.timeout)
        except (TimeoutError, socket.timeout):
            return ReviewCall('timeout')
        except (urllib.error.URLError, OSError, HTTPException):
            return ReviewCall('network_error')
        if self.clock()-started > self.timeout:
            return ReviewCall('timeout')
        if not 200 <= response.status_code < 300:
            code = JevDecisionProvider._http_error(response.status_code)
            if code not in {'auth_failed', 'access_denied', 'insufficient_credits', 'rate_limited',
                            'timeout', 'provider_unavailable'}:
                code = 'http_error'
            try:
                delay = float(next((v for k, v in response.headers.items() if k.lower() == 'retry-after'), '0'))
                delay = max(0, delay) if math.isfinite(delay) else 0
            except (ValueError, TypeError):
                delay = 0
            return ReviewCall(code, retry_after=delay)
        try:
            if len(response.body) > 1_000_000:
                return ReviewCall('invalid_response')
            payload = json.loads(response.body)
        except (ValueError, TypeError):
            return ReviewCall('invalid_response')
        answer = parse_answer(payload)
        cost = response_cost(payload)
        if answer is None:
            return ReviewCall('invalid_response', cost_usd=cost)
        if not pinned_returned_model(answer.model):
            return ReviewCall('model_mismatch', cost_usd=cost)
        return ReviewCall('success', payload, cost_usd=cost)


def collect_missing(states, cache, provider, *, interval=1, retries=2, circuit_failures=3, sleep=time.sleep,
                    budget=None):
    """Bounded sequential calls; each success commits before the next request, resumable after interruption."""
    if not math.isfinite(interval) or not 0 < interval <= 60 or not 0 <= retries <= 2 or not 1 <= circuit_failures <= 10:
        raise ValueError('invalid collection limits')
    unique = {answer_key(s): tuple(s) for s in states}
    pending = [s for _, s in sorted(unique.items()) if cache.get(s) is None]
    result = dict(successful=0, network_calls=0, remaining=len(pending), circuit_open=False,
                  stopped_reason=None, errors={})
    failures, delay = 0, interval
    fatal = {'auth_failed', 'access_denied', 'insufficient_credits', 'model_mismatch'}
    retryable = {'timeout', 'network_error', 'rate_limited', 'provider_unavailable'}
    for state in pending:
        for attempt in range(retries+1):
            if result['network_calls']:
                sleep(delay)
            reservation = budget.reserve() if budget is not None else None
            if budget is not None and reservation is None:
                result['stopped_reason'] = 'cost_limit'
                return result
            call = provider.request(state)
            result['network_calls'] += 1
            if budget is not None:
                budget.settle(reservation, call.cost_usd)
            delay = interval
            if call.status == 'success':
                cache.put(state, call.payload)
                cache.record_attempt(state, 'success', attempts=attempt+1)
                result['successful'] += 1
                result['remaining'] -= 1
                failures = 0
                break
            failures += 1
            result['errors'][call.status] = result['errors'].get(call.status, 0)+1
            stop = call.status in fatal or call.retry_after > 60 or failures >= circuit_failures
            if stop or attempt == retries or call.status not in retryable:
                cache.record_attempt(state, call.status, attempts=attempt+1)
                if stop:
                    result['circuit_open'] = failures >= circuit_failures
                    result['stopped_reason'] = call.status
                    return result
                break
            delay = max(interval, 2**attempt, call.retry_after)
    return result
