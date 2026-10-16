from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D

import pytest

from intraday import setup2
from intraday.contracts import DecisionScope, ScopedRuleCandidate, SpotRuleParameters, PerpRuleParameters, Regime
from intraday.jev_review_overlay import ReviewOverlay, compare_routes
from intraday.replay_v2.contracts import ReplayConfig, ReplayDataset, binance_gate_profile
from intraday.replay_v2.engine import simulate
from intraday.replay_v2.metrics import encoded, fingerprint
from test_setup2_parity import FIRST, walk
from test_setup2_perp import bearish


@pytest.fixture(scope='module', params=[1, -1])
def route(request):
    side = request.param
    if side > 0:
        large, small = walk(29)
        parameters = SpotRuleParameters(entry_profile='setup2_v1', entry_window=30, exit_window=10)
    else:
        large, _, small_rows = bearish()
        small = setup2.bars(small_rows, setup2.M15)
        parameters = PerpRuleParameters(entry_profile='setup2_v1', allowed_regimes=(Regime.TRENDING_DOWN, Regime.SIDEWAYS))
    rule = ScopedRuleCandidate.create(rule_id='review-route-'+str(side), parent_rule_id='bootstrap', thesis_id='review',
        symbol='ETHUSDT', scope=DecisionScope.SPOT_4H if side > 0 else DecisionScope.PERP_INTRADAY,
        parameters=parameters, created_at=FIRST, model_ref='operator/test', prompt_version='review-test')
    config = ReplayConfig(symbol='ETHUSDT', market='spot' if side > 0 else 'perp', rule_id=rule.rule_id,
        start=FIRST+600*setup2.H4, end=large[-1].available_at, leverage=1, profile=binance_gate_profile())
    data = ReplayDataset(rule=rule, candles=large, profile_candles=tuple(small), indicator_anchor=FIRST)
    report = simulate(config, data)
    assert report['trades']
    return config, data, report


def test_disabled_overlay_preserves_whole_output_bytes(route):
    config, data, baseline = route
    assert data.jev_review is None
    assert encoded(simulate(config, replace(data, jev_review=None))) == encoded(baseline)
    assert 'jev_review' not in baseline['inputs']


def test_no_view_preserves_events_trades_summary_but_binds_missing_cache(route):
    config, data, baseline = route
    overlay = ReviewOverlay((), 'a'*64)
    report = simulate(config, replace(data, jev_review=overlay))
    assert report['result_id'] != baseline['result_id']
    assert report['summary'] == baseline['summary']
    assert report['events'] == baseline['events'] and report['trades'] == baseline['trades']
    assert report['inputs']['jev_review']['answers_sha256'] == 'a'*64
    assert simulate(config, replace(data, jev_review=overlay)) == report
    other = replace(overlay, answers_sha256='b'*64)
    assert simulate(config, replace(data, jev_review=other))['result_id'] != report['result_id']


def test_veto_and_reduce_at_rule_bar_close_and_counterfactual_join(route):
    config, data, baseline = route
    opened = baseline['trades'][0]['entered_at']
    # Next H4 open and trigger close are the same instant, not the trigger open.
    labels = ((data.rule.rule_id, opened, 'VETO'),)
    veto = simulate(config, replace(data, jev_review=ReviewOverlay(labels, 'c'*64)))
    assert veto['summary']['blocked_entries']['jev_review_veto'] >= 1
    assert not any(t['entered_at'] == opened for t in veto['trades'])
    reduced = simulate(config, replace(data, jev_review=ReviewOverlay(
        ((data.rule.rule_id, opened, 'REDUCE'),), 'c'*64)))
    base_entry = next(e for e in baseline['events'] if e['kind'] == 'entry')
    small_entry = next(e for e in reduced['events'] if e['kind'] == 'entry')
    # Division is independently rounded under the engine's 28-digit Decimal context.
    assert abs(D(small_entry['quantity'])-D(base_entry['quantity'])/2) <= D('1e-26')
    assert D(small_entry['risk_audit']['notional']) <= D(base_entry['risk_audit']['notional'])/2+D('1e-24')
    comparison = compare_routes(baseline, veto, ReviewOverlay(labels, 'c'*64))
    first = next(r for r in comparison if r['bar_close'] == opened)
    assert first['rule_id'] == data.rule.rule_id and first['label_a'] == 'VETO'
    assert first['rule_only_pnl'] == baseline['trades'][0]['net_known_pnl']
    assert first['overlay_pnl'] is None


def test_overlay_rejects_duplicate_or_unbounded_labels():
    with pytest.raises(ValueError):
        ReviewOverlay((('r', FIRST.isoformat(), 'INCREASE'),), 'a'*64)
    with pytest.raises(ValueError):
        ReviewOverlay((('r', FIRST.isoformat(), 'VETO'),)*2, 'a'*64)
    with pytest.raises(ValueError):
        ReviewOverlay((), 'no-checksum')


def test_every_exposed_veto_gets_a_real_counterfactual_trade(route, tmp_path):
    from datetime import datetime
    from intraday.jev_review import paired_replay, scan_route
    from intraday.jev_review_cache import AnswerCache
    from test_setup2_review import payload
    config, data, baseline = route
    signals = scan_route(config, data)
    p = payload()
    p['answers']['action'] = dict(choice='skip', probabilities=dict(enter_full=.05, reduce=.05, skip=.9))
    with AnswerCache(tmp_path/'cache.sqlite3', writable=True) as cache:
        for tokens in {s.tokens for s in signals}: cache.put(tokens, p)
    with AnswerCache(tmp_path/'cache.sqlite3') as cache:
        pair = paired_replay(config, data, signals, cache)
    vetoes = [e for e in pair['reviewed']['events'] if e.get('reason') == 'jev_review_veto']
    rows = pair['counterfactual']
    assert len(vetoes) == len(rows) and len(vetoes) > len(baseline['trades'])
    assert all(r['counterfactual_pnl'] is not None for r in rows)
    uncovered = [r for r in rows if not r['reference_trade_available']]
    assert uncovered and all(r['counterfactual_method'] == 'isolated_flat_start' for r in uncovered)
    first = uncovered[0]
    standalone = simulate(config.model_copy(update={'start': datetime.fromisoformat(first['bar_close'])}), data)
    assert first['counterfactual_pnl'] == standalone['trades'][0]['net_known_pnl']
    assert len(first['counterfactual_id']) == 64
