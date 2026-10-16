import json
from dataclasses import replace

import pytest

from intraday.jev_review_provider import ReviewProvider, collect_missing
from intraday.jev_review_cache import AnswerCache
from intraday.provider_client import HttpResponse
from intraday.provider_profiles import ProviderCredential
from intraday.setup2_review import encode_state
from test_intraday_jev_provider import profile
from test_setup2_review import observation, history, payload

TOKENS = tuple(encode_state(observation(), history()))


def provider(transport, **kwargs):
    return ReviewProvider(ProviderCredential(profile(), 'fake-test-only'), transport=transport, **kwargs)


def test_provider_reuses_transport_and_credential_without_intraday_database():
    requests = []
    def fake(**request):
        requests.append(request)
        return HttpResponse(200, {}, json.dumps(payload()).encode())
    result = provider(fake).request(TOKENS)
    assert result.status == 'success' and result.payload['model'] == payload()['model']
    request = requests[0]
    assert request['url'] == profile().base_url
    assert request['headers']['Authorization'] == 'Bearer fake-test-only'
    body = json.loads(request['body'])
    assert body['state'] == {'tokens': list(TOKENS)}
    assert set(body['questions']) == {'action', 'exhaustion', 'disorderly'}
    assert body['model'] == 'typesafe/jev-1.13'
    assert 'setup2-review' in request['headers']['User-Agent']


@pytest.mark.parametrize('status,body,expected', [
    (200, b'not-json', 'invalid_response'),
    (200, json.dumps(payload(model='typesafe/jev-1.14')).encode(), 'model_mismatch'),
    (402, b'private provider text', 'insufficient_credits'),
    (429, b'private provider text', 'rate_limited'),
    (503, b'private provider text', 'provider_unavailable'),
])
def test_provider_returns_redacted_no_opinion_for_errors(status, body, expected):
    result = provider(lambda **kw: HttpResponse(status, {}, body)).request(TOKENS)
    assert result.status == expected and result.payload is None


def test_timeout_and_elapsed_deadline_return_no_opinion():
    def timeout(**kw): raise TimeoutError('private details')
    assert provider(timeout).request(TOKENS).status == 'timeout'
    times = iter((0., 6.))
    p = provider(lambda **kw: HttpResponse(200, {}, json.dumps(payload()).encode()),
                 clock=lambda: next(times), timeout_seconds=5)
    assert p.request(TOKENS).status == 'timeout'


def test_collector_retries_with_limits_resumes_and_circuit_breaks(tmp_path):
    seen, sleeps = [], []
    def fake(**kw):
        seen.append(kw)
        return HttpResponse(429, {}, b'') if len(seen) == 1 else HttpResponse(200, {}, json.dumps(payload()).encode())
    with AnswerCache(tmp_path/'cache.sqlite3', writable=True) as cache:
        out = collect_missing([TOKENS, TOKENS], cache, provider(fake), sleep=sleeps.append, interval=1, retries=2)
        assert out['successful'] == 1 and out['network_calls'] == 2
        assert sleeps and cache.get(TOKENS) is not None
        resumed = collect_missing([TOKENS], cache, provider(fake), sleep=sleeps.append)
        assert resumed['network_calls'] == 0 and len(seen) == 2
    states = []
    for token in ('adx_weak', 'adx_strong', 'adx_extreme', 'adx_unknown'):
        tokens = list(TOKENS)
        tokens[3] = token
        states.append(tokens)
    with AnswerCache(tmp_path/'errors.sqlite3', writable=True) as cache:
        broken = provider(lambda **kw: HttpResponse(503, {}, b''))
        out = collect_missing(states, cache, broken, sleep=sleeps.append, retries=0, circuit_failures=3)
        assert out['circuit_open'] and out['network_calls'] == 3 and out['remaining'] == 4


def test_fatal_credit_error_stops_without_retry_and_state_must_be_anonymous(tmp_path):
    with AnswerCache(tmp_path/'cache.sqlite3', writable=True) as cache:
        out = collect_missing([TOKENS], cache, provider(lambda **kw: HttpResponse(402, {}, b'')),
                              sleep=lambda _: None)
        assert out['network_calls'] == 1 and out['stopped_reason'] == 'insufficient_credits'
    with pytest.raises(ValueError, match='state'):
        provider(lambda **kw: pytest.fail('network must not be reached')).request(['ETHUSDT', '2026'])
