"""Stage B: Setup-2 storage, soak observation and v2 replay gate on synthetic stored bars."""
from datetime import timedelta
from decimal import Decimal as D

import pytest

from intraday import active_set, setup2, setup2_store
from intraday.contracts import DecisionScope, SpotRuleParameters, SpotRuleProposal
from intraday.replay_v2.artifacts import read_report, read_series
from intraday.replay_v2.lifecycle import replay_gate
from intraday.setup2_cli import candidate_rule
from intraday.store import IntradayStore
from test_multi_asset_runtime import Provider, snapshot
from test_setup2_parity import FIRST, walk

COINS = ('ETHUSDT', 'NEARUSDT', 'SOLUSDT')


@pytest.fixture(scope='module')
def series():
    return walk(7)


@pytest.fixture
def store(tmp_path, series):
    store = IntradayStore(tmp_path/'intraday.sqlite')
    plan = active_set.equal_plan(COINS, anchor=FIRST)
    active_set.switch(store, plan, actor='julius', reason='stage B test', now=FIRST,
                      execution_check=lambda: None, initial=True)
    large, small = series
    setup2_store.record_candles(store, 'ETHUSDT', 'spot', '4h', [c.row() for c in large])
    setup2_store.record_candles(store, 'ETHUSDT', 'spot', '15m', [c.row() for c in small])
    return store


def test_legacy_rule_payloads_are_unchanged_and_models_cannot_pick_setup2():
    assert 'entry_profile' not in SpotRuleParameters().model_dump(mode='json')
    rule = SpotRuleParameters(entry_profile='setup2_v1', entry_window=30, exit_window=10)
    assert SpotRuleParameters.model_validate_json(rule.model_dump_json()) == rule
    with pytest.raises(ValueError, match='locked to Donchian 30/10'):
        SpotRuleParameters(entry_profile='setup2_v1')
    with pytest.raises(ValueError, match='legacy Donchian'):
        SpotRuleProposal(parameters=rule, rationale='a model must not select the Setup-2 profile')
    from intraday.spot_signal import evaluate_donchian
    with pytest.raises(ValueError, match='intraday.setup2'):
        evaluate_donchian([[0, 1, 1, 1, 1, 1, 1]]*40, rule)


def test_stored_bars_are_immutable_and_evaluate_like_the_live_function(store, series):
    large, small = series
    row = large[10].row()
    setup2_store.record_candles(store, 'ETHUSDT', 'spot', '4h', [row])  # Same payload is a no-op.
    with pytest.raises(ValueError, match='bar changed'):
        setup2_store.record_candles(store, 'ETHUSDT', 'spot', '4h', [[*row[:4], '1', *row[5:]]])
    now = large[-1].available_at+timedelta(minutes=1)
    stored = setup2_store.evaluate(store, 'ETHUSDT', 'spot', anchor=FIRST, now=now)
    direct = setup2.evaluate_setup2(setup2.bars([c.row() for c in large], setup2.H4),
                                    setup2.bars([c.row() for c in small], setup2.M15), side=1, anchor=FIRST)
    assert stored == direct and stored.bar_close == large[-1].available_at


def test_sync_fetches_only_missing_closed_bars(store, series):
    large, small = series
    calls = []

    def fetch(symbol, interval, start, end, *, now):
        calls.append((interval, start, end))
        source = large if interval == '4h' else small
        rows = [c.row() for c in source if start <= c.opened_at and c.available_at <= end]
        return type('Snapshot', (), {'raw_rows': rows})()
    now = large[-1].available_at+timedelta(hours=1)
    assert setup2_store.sync(store, 'ETHUSDT', 'spot', anchor=FIRST, now=now, fetch_spot=fetch) == {'4h': 0, '15m': 0}
    assert calls == []


def test_soak_calls_jev_only_for_an_active_full_setup(store, series, monkeypatch):
    large, _ = series
    now = large[-1].available_at+timedelta(minutes=1)
    rule = candidate_rule(store, 'ETHUSDT', 'spot', actor='julius', reason='Setup-2 candidate for ETH', now=now)
    provider = Provider()
    observed = setup2_store.evaluate(store, 'ETHUSDT', 'spot', anchor=FIRST, now=now)
    forced = observed.__class__(**{**observed.__dict__, 'entry': True})
    monkeypatch.setattr(setup2_store, 'evaluate', lambda *a, **k: forced)
    status = setup2_store.run_setup2_observation(store, provider, snapshot('ETHUSDT', DecisionScope.SPOT_4H),
                                                 rule=rule, market='spot', now=now)
    assert status == 'success' and provider.calls == [('ETHUSDT', DecisionScope.SPOT_4H)]
    monkeypatch.setattr(setup2_store, 'evaluate', lambda *a, **k: observed.__class__(
        **{**observed.__dict__, 'entry': False}))
    later = now+setup2.H4
    assert setup2_store.run_setup2_observation(store, provider, snapshot('ETHUSDT', DecisionScope.SPOT_4H),
                                               rule=rule, market='spot', now=later) == 'skipped_no_setup'
    assert len(provider.calls) == 1
    rows = setup2_store.observations(store, 'ETHUSDT', 'spot', rule_id=rule.rule_id)
    assert [r['jev_status'] for r in rows] == ['called']  # Same bar close: one heartbeat row per bar.


def test_setup2_replay_gate_uses_anchored_bars_split_caps_and_capped_trail(store, series, tmp_path):
    large, _ = series
    now = large[-1].available_at+timedelta(minutes=1)
    rule = candidate_rule(store, 'ETHUSDT', 'spot', actor='julius', reason='Setup-2 candidate for ETH', now=now)
    evaluation = replay_gate(store, rule.rule_id, now=now, report_dir=tmp_path/'reports')
    report = read_report(tmp_path/'reports', evaluation.run_id)
    assert report['inputs']['rule_parameters']['entry_profile'] == 'setup2_v1'
    assert report['config']['start'] == (FIRST+600*setup2.H4).isoformat().replace('+00:00', 'Z')
    assert {'setup2_jev_filter_not_replayed', 'setup2_m15_profile_uniform_volume'} <= set(report['limitations'])
    assert report['methodology']['risk_limits']['max_drawdown_pct'] == 15
    assert evaluation.metrics['history_bars'] == len(large)
    events = read_series(tmp_path/'reports', evaluation.run_id, 'events', limit=1000)['items']
    trades = read_series(tmp_path/'reports', evaluation.run_id, 'trades', limit=1000)['items']
    for event in events:
        if event['kind'] == 'entry':
            notional = D(event['risk_audit']['notional'])
            assert notional <= D(event['risk_audit']['pre_fill_equity'])*D('.6')/3
            assert D(event['stop']) >= D(event['price'])*D('.9')
    assert {t['exit_reason'] for t in trades} <= {'donchian_exit', 'trailing_stop', 'trailing_stop_gap',
                                                            'window_end', 'daily_loss_limit', 'max_drawdown'}
    assert trades and 'minimum_600_anchored_h4_bars' not in evaluation.reason_codes
