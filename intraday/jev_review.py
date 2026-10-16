"""Offline Setup-2 signal scan, collection estimate and paired replay orchestration."""

from bisect import bisect_right
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from math import ceil
from pathlib import Path

from intraday import active_set, setup2, setup2_store
from intraday.jev_review_cache import answer_key, canonical, digest
from intraday.jev_review_overlay import ReviewOverlay, compare_routes
from intraday.jev_review_provider import request_payload
from intraday.replay_v2.contracts import ReplayConfig, binance_gate_profile
from intraday.replay_v2.data import load_dataset
from intraday.replay_v2.engine import simulate
from intraday.replay_v2.funding import FundingSnapshot
from intraday.replay_v2.lifecycle import _setup2_inputs
from intraday.replay_v2.metrics import dataset_fingerprint
from intraday.setup2_review import (CUT_POINTS, MODEL, QUESTION_SET_VERSION, STATE_VERSION, LabelConfig, encode_state, questions)
from intraday.store import IntradayStore

INPUT_USD_PER_MILLION = Decimal('.042')
PRICE_SOURCE = 'https://openrouter.ai/api/v1/models/typesafe/jev-1.13/endpoints'
PRICE_OBSERVED = '2026-10-16'


@dataclass(frozen=True)
class ReviewSignal:
    rule_id: str
    bar_close: datetime
    tokens: tuple[str, ...]

    def payload(self):
        return dict(rule_id=self.rule_id, bar_close=self.bar_close.isoformat(), state=list(self.tokens),
                    key=answer_key(self.tokens))


def _plan_identity(plan):
    # Approval binds requests/data, not mutable cache coverage. A partial fill must be resumable
    # under the same approval, while any changed question, state, route or price requires a new plan.
    fields = ('workflow', 'model', 'state_version', 'question_set_version', 'cut_points',
              'request_template_sha256', 'route_inputs', 'signals', 'entry_bars', 'unique_states',
              'tuning_end_exclusive', 'test_start', 'input_usd_per_million', 'output_usd_per_million',
              'price_source', 'price_observed_at', 'token_estimate_method')
    return {k: plan[k] for k in fields}


def valid_plan(plan):
    try:
        return digest(_plan_identity(plan)) == plan['plan_hash'] and digest(
            {k:v for k,v in plan.items() if k != 'snapshot_sha256'}) == plan['snapshot_sha256']
    except (KeyError, TypeError, ValueError):
        return False


def scan_route(config, data):
    """Every entry=True bar, regardless of position occupancy; no PnL is inspected."""
    side = 1 if config.market == 'spot' else -1
    series, observations = setup2.series_observations(list(data.candles), list(data.profile_candles),
        side=side, anchor=data.indicator_anchor, profile=data.rule.parameters.entry_profile,
        warmup_bars=data.warmup_bars or setup2.WARMUP_BARS, own_from=data.price_proxy_until)
    if series is None:
        raise ValueError('review history is not anchored')
    funding = config.profile.funding
    settlements = funding.settlements if side < 0 and funding else ()
    times = [s.at for s in settlements]
    result = []
    for i, obs in enumerate(observations):
        if obs is None or not obs.entry or not config.start <= obs.bar_close < config.end:
            continue
        k = bisect_right(times, obs.bar_close)
        settled = settlements[k-1] if k else None
        result.append(ReviewSignal(data.rule.rule_id, obs.bar_close, tuple(encode_state(obs, series[:i+1], settled))))
    return tuple(result)


def build_plan(signals, cache, *, route_inputs=()):
    signals = tuple(signals)
    unique = {answer_key(s.tokens): s.tokens for s in signals}
    missing = {key: state for key, state in sorted(unique.items()) if cache.get(state) is None}
    sizes = [len(canonical(request_payload(s)).encode()) for s in missing.values()]
    tokens = sum(ceil(n/4) for n in sizes)
    # The provider tokenizer is not shipped here. This is a transparent proxy, not measured usage.
    budget_tokens = sum(n+1024 for n in sizes)
    payload = dict(research_only=True, workflow='setup2_review', model=MODEL, state_version=STATE_VERSION,
        question_set_version=QUESTION_SET_VERSION, cut_points=CUT_POINTS,
        request_template_sha256=digest(questions()),
        entry_bars=len(signals), unique_states=len(unique), missing_states=len(missing),
        cached_states=len(unique)-len(missing), planned_network_calls=len(missing), network_calls=0,
        estimated_input_tokens=tokens, budget_input_tokens=budget_tokens,
        token_estimate_method='ceil(canonical UTF-8 request bytes / 4); budget uses bytes + 1024 per request',
        estimated_cost_usd=float(Decimal(tokens)*INPUT_USD_PER_MILLION/1_000_000),
        budget_cost_usd=float(Decimal(budget_tokens)*INPUT_USD_PER_MILLION/1_000_000),
        max_cost_with_two_retries_usd=float(3*Decimal(budget_tokens)*INPUT_USD_PER_MILLION/1_000_000),
        input_usd_per_million=str(INPUT_USD_PER_MILLION), output_usd_per_million='0',
        price_source=PRICE_SOURCE, price_observed_at=PRICE_OBSERVED,
        tuning_end_exclusive='2026-01-01T00:00:00+00:00', test_start='2026-01-01T00:00:00+00:00',
        route_inputs=list(route_inputs), signals=[s.payload() for s in signals],
        missing_keys=sorted(missing), used_answers_sha256=cache.used_digest(unique.values()))
    result = dict(payload, plan_hash=digest(_plan_identity(payload)))
    return dict(result, snapshot_sha256=digest(result))


def database_routes(database, end, *, funding_dir):
    """Read the exact copied candidates/settings, never changes an active set or gate."""
    store = IntradayStore(database, read_only=True)
    with store.read_snapshot():
        version = active_set.current(store)
        if version is None:
            raise ValueError('research database requires copied ADR-007 settings')
        for symbol, coin in sorted(version.plan.coins.items()):
            # Include all ten study routes, including HYPE Spot with its documented proxy.
            for market in ('spot', 'perp'):
                scope = setup2_store.SCOPES[market]
                rules = [store.load_scoped_rule(r['id']) for r in store.list_scoped_rules(scope, symbol=symbol)]
                rules = [r for r in rules if r.parameters.entry_profile == version.plan.rule_profile]
                if len(rules) != 1:
                    raise ValueError(f'expected one copied study candidate for {symbol} {market}')
                rule = rules[0]
                start, finish, _ = _setup2_inputs(store, rule, market, end)
                if finish != end:
                    raise ValueError(f'route history does not reach requested end: {symbol} {market}')
                funding = None
                if market == 'perp':
                    path = Path(funding_dir)/f'{symbol}-funding-public.json'
                    funding = FundingSnapshot.model_validate_json(path.read_bytes()).history
                    if funding.coverage_start > start or funding.coverage_end < finish:
                        raise ValueError('funding must cover the complete route window')
                config = ReplayConfig(symbol=symbol, market=market, rule_id=rule.rule_id,
                    start=start, end=finish, capital=1000, leverage=1, profile=binance_gate_profile(funding=funding))
                yield config, load_dataset(store.database, config, reader=store)


def route_identity(config, data, signals):
    return dict(symbol=config.symbol, market=config.market, config=config.model_dump(mode='json'),
                dataset_checksum=dataset_fingerprint(data), entry_bars=len(signals),
                rule_content_hash=data.rule.content_hash)


def paired_replay(config, data, signals, cache, *, label_config=LabelConfig()):
    from intraday.jev_review_counterfactual import supplement_counterfactuals
    data = replace(data, jev_review=None)
    overlay = ReviewOverlay.from_cache(((s.rule_id, s.bar_close, s.tokens) for s in signals), cache, config=label_config)
    baseline = simulate(config, data)
    reviewed = simulate(config, replace(data, jev_review=overlay))
    compared = compare_routes(baseline, reviewed, overlay)
    compared = supplement_counterfactuals(config, data, compared, baseline['inputs']['dataset_checksum'])
    return dict(rule_only=baseline, reviewed=reviewed, counterfactual=compared, overlay=overlay.payload())
