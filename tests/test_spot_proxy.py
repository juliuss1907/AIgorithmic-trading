"""Perp price proxy, per-coin warm-up and the scoped closed-trades waiver for a young Spot market (ADR-007)."""
from datetime import timedelta
from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from intraday import active_set, setup2, setup2_store
from intraday.active_set import CoinPlan
from intraday.replay_v2.artifacts import read_report
from intraday.replay_v2.lifecycle import replay_gate
from intraday.setup2_cli import candidate_rule
from intraday.store import IntradayStore
from test_setup2_parity import FIRST, walk

SYMBOL = 'HYPEUSDT'
PROFILE = 'setup2_slots_v1'


@pytest.fixture(scope='module')
def series():
    return walk(7)


def build(tmp_path, series, *, own_bars=172, warmup=400, waive=True, drop_perp_bar=None):
    """HYPE with `own_bars` Spot H4 bars (and their M15) after a boundary; Perp has the whole history."""
    large, small = series
    boundary = large[len(large)-own_bars].opened_at
    store = IntradayStore(tmp_path/'intraday.sqlite')
    plan = active_set.slots_plan({SYMBOL: 'both'}, anchors={SYMBOL: FIRST}, warmup={SYMBOL: warmup},
                                 proxy={SYMBOL: boundary}, waive={SYMBOL: waive})
    active_set.switch(store, plan, actor='julius', reason='proxy test', now=FIRST, execution_check=lambda: None,
                      initial=True)
    perp = [c.row() for i, c in enumerate(large) if i != drop_perp_bar]
    setup2_store.record_candles(store, SYMBOL, 'perp', '4h', perp)
    setup2_store.record_candles(store, SYMBOL, 'spot', '4h', [c.row() for c in large if c.opened_at >= boundary])
    setup2_store.record_candles(store, SYMBOL, 'spot', '15m', [c.row() for c in small if c.opened_at >= boundary])
    return store, boundary


def test_plan_fields_are_validated_and_legacy_payloads_unchanged():
    base = dict(mode='both', spot_weight=D(1), perp_weight=D(1), indicator_anchor=FIRST)
    boundary = FIRST+500*setup2.H4
    assert 'warmup_bars' not in CoinPlan(**base).model_dump(mode='json')
    kept = CoinPlan(**base, warmup_bars=400, spot_price_proxy_until=boundary, waive_min_trades=('spot',))
    assert CoinPlan.model_validate_json(kept.model_dump_json()) == kept
    for bad in (dict(warmup_bars=399), dict(warmup_bars=601), dict(waive_min_trades=('spot',)),
                dict(spot_price_proxy_until=boundary+timedelta(hours=1)),
                dict(spot_price_proxy_until=FIRST), dict(mode='perp', spot_weight=D(0), perp_weight=D(1),
                                                         spot_price_proxy_until=boundary)):
        with pytest.raises(ValidationError):
            CoinPlan(**{**base, **bad})


def test_the_proxied_series_is_perp_price_with_zero_volume_before_the_boundary(tmp_path, series):
    store, boundary = build(tmp_path, series)
    large, _ = series
    bars = setup2_store.proxied_bars(store, SYMBOL, 'spot', '4h', since=FIRST)
    cut = len([b for b in bars if b.opened_at < boundary])
    assert len(bars) == len(large) and cut == len(large)-172
    assert all(b.volume == 0 for b in bars[:cut]) and all(b.volume == c.volume for b, c in zip(bars[cut:], large[cut:]))
    assert [b.close for b in bars] == [c.close for c in large]
    assert setup2.anchored(bars, FIRST) is not None
    # The stored Spot market is untouched, and Perp reads are never proxied.
    assert len(setup2_store.list_bars(store, SYMBOL, 'spot', '4h')) == 172
    assert len(setup2_store.proxied_bars(store, SYMBOL, 'perp', '4h', since=FIRST)) == len(large)
    broken, _ = build(tmp_path/'gap', series, drop_perp_bar=50)
    assert setup2.anchored(setup2_store.proxied_bars(broken, SYMBOL, 'spot', '4h', since=FIRST), FIRST) is None


def test_no_entry_exists_until_volume_and_profile_are_the_spot_markets_own(tmp_path, series):
    store, boundary = build(tmp_path, series)
    settings = setup2_store.coin_settings(store, SYMBOL)
    bars = setup2_store.proxied_bars(store, SYMBOL, 'spot', '4h', since=FIRST, settings=settings)
    m15 = setup2_store.list_bars(store, SYMBOL, 'spot', '15m', since=setup2_store.profile_start(FIRST, 400))
    _, observed = setup2.series_observations(bars, m15, side=1, anchor=FIRST, profile=PROFILE, warmup_bars=400,
                                             own_from=boundary)
    assert observed[398] is None and observed[399] is not None          # the warm-up is 400 bars for this coin
    cut = len(bars)-172
    for i, obs in enumerate(observed):
        if obs is None:
            continue
        if bars[i].opened_at < boundary+20*setup2.H4:
            assert not obs.entry and 'proxy_volume_warmup' in obs.blockers or bars[i].opened_at < boundary
        if bars[i].opened_at < boundary:
            assert not obs.entry and 'profile_incomplete' in obs.blockers
    assert 'proxy_volume_warmup' in observed[cut].blockers
    assert 'proxy_volume_warmup' not in observed[cut+20].blockers
    default = setup2.series_observations(bars, m15, side=1, anchor=FIRST, profile=PROFILE)[1]
    assert default[398] is None and default[399] is None and default[599] is not None   # 600 for every other coin


def test_the_gate_starts_at_anchor_plus_the_coins_warmup_and_waives_only_the_trades_criterion(tmp_path, series):
    store, _ = build(tmp_path, series)
    large, _ = series
    now = large[-1].available_at+timedelta(minutes=1)
    rule = candidate_rule(store, SYMBOL, 'spot', actor='julius', reason='Setup-2 slot candidate for HYPE', now=now,
                          profile=PROFILE)
    evaluation = replay_gate(store, rule.rule_id, now=now, report_dir=tmp_path/'reports')
    report = read_report(tmp_path/'reports', evaluation.run_id)
    assert report['config']['start'] == (FIRST+400*setup2.H4).isoformat().replace('+00:00', 'Z')
    assert 'setup2_spot_price_proxied_from_perp' in report['limitations']
    assert evaluation.metrics['required_bars'] == 400 and evaluation.metrics['min_trades_waived'] == 1
    assert 'minimum_6_closed_trades' not in evaluation.reason_codes
    assert 'minimum_400_anchored_h4_bars' not in evaluation.reason_codes
    if evaluation.metrics['closed_trades'] < 6:
        assert evaluation.metrics['waived_minimum_6_closed_trades'] == 1
    # The same data without the waiver keeps the criterion.
    control, _ = build(tmp_path/'control', series, waive=False)
    rule = candidate_rule(control, SYMBOL, 'spot', actor='julius', reason='Setup-2 slot candidate for HYPE', now=now,
                          profile=PROFILE)
    again = replay_gate(control, rule.rule_id, now=now, report_dir=tmp_path/'control-reports')
    assert again.metrics['min_trades_waived'] == 0
    if again.metrics['closed_trades'] < 6:
        assert 'minimum_6_closed_trades' in again.reason_codes


def test_the_waiver_lapses_once_the_spot_market_has_enough_bars_of_its_own(tmp_path, series):
    store, _ = build(tmp_path, series, own_bars=500)             # 500 Spot bars of its own against a 400 warm-up
    large, _ = series
    now = large[-1].available_at+timedelta(minutes=1)
    rule = candidate_rule(store, SYMBOL, 'spot', actor='julius', reason='Setup-2 slot candidate for HYPE', now=now,
                          profile=PROFILE)
    evaluation = replay_gate(store, rule.rule_id, now=now, report_dir=tmp_path/'reports')
    assert evaluation.metrics['min_trades_waived'] == 0


def test_sync_fetches_a_proxied_spot_market_only_from_its_first_own_bar(tmp_path, series):
    large, small = series
    boundary = large[len(large)-172].opened_at
    store = IntradayStore(tmp_path/'intraday.sqlite')
    plan = active_set.slots_plan({SYMBOL: 'both'}, anchors={SYMBOL: FIRST}, warmup={SYMBOL: 400},
                                 proxy={SYMBOL: boundary}, waive={SYMBOL: True})
    active_set.switch(store, plan, actor='julius', reason='sync test', now=FIRST, execution_check=lambda: None,
                      initial=True)
    calls, probes = [], []

    def fetch(symbol, interval, start, end, *, now):
        calls.append((interval, start))
        return type('Snapshot', (), {'raw_rows': []})()

    def probe(symbol, interval, at):
        probes.append((interval, at))
        return at+timedelta(hours=3) if interval == '15m' else at    # trading began three hours into the first bar
    now = large[-1].available_at+timedelta(hours=8)
    setup2_store.sync(store, SYMBOL, 'spot', anchor=FIRST, now=now, fetch_spot=fetch, probe_spot=probe)
    assert dict(calls) == {'4h': boundary, '15m': boundary+timedelta(hours=3)}
    assert {interval: at for interval, at in probes} == {'4h': boundary, '15m': boundary}
    # Perp is never probed or clipped to the boundary: it starts at the anchor.
    calls.clear()
    perp = lambda *a, **k: type('Snapshot', (), {'raw_rows': []})()
    setup2_store.sync(store, SYMBOL, 'perp', anchor=FIRST, now=now, fetch_perp=perp, probe_spot=probe)
    assert len(probes) == 2


def test_readiness_counts_the_proxy_and_asks_for_twenty_days_of_the_markets_own_bars(tmp_path, series):
    store, boundary = build(tmp_path, series, own_bars=172)
    plan = active_set.current(store).plan
    coin = plan.coins[SYMBOL]
    now = series[0][-1].available_at
    # Spot candles for history_bars come from the asset-candle table; the helper reads Perp before the boundary.
    ready, own = active_set.spot_history(store, SYMBOL, coin, now)
    perp_before = len([c for c in series[0] if c.opened_at < boundary])
    assert (ready-own, own) == (perp_before, own)


def test_waiver_cases_zero_trades_pass_losses_and_drawdown_still_reject(tmp_path, series):
    import copy
    from intraday.replay_v2.gates import GateEvidence, evaluate_report_gate
    store, _ = build(tmp_path, series)
    now = series[0][-1].available_at+timedelta(minutes=1)
    rule = candidate_rule(store, SYMBOL, 'spot', actor='julius', reason='Setup-2 slot candidate for HYPE', now=now,
                          profile=PROFILE)
    ran = replay_gate(store, rule.rule_id, now=now, report_dir=tmp_path/'reports')
    report = read_report(tmp_path/'reports', ran.run_id)
    waived = GateEvidence(history_bars=672, required_bars=400, history_coverage=1, latest_candle_age_seconds=60,
                          min_trades_waived=True)
    strict = waived.model_copy(update={'min_trades_waived': False})

    def judged(evidence, **summary):
        edited = copy.deepcopy(report)
        edited['summary'].update(summary)
        return evaluate_report_gate(edited, evidence)
    zero = judged(waived, closed_trades=0, net_return_pct=0.0, max_drawdown_known_pct=0.0)
    assert zero.status == 'pass' and zero.metrics['waived_nonpositive_net_return_without_trades'] == 1
    assert judged(waived, closed_trades=3, net_return_pct=-1.0, max_drawdown_known_pct=2.0).reason_codes == (
        'nonpositive_net_return',)
    assert judged(waived, closed_trades=3, net_return_pct=5.0, max_drawdown_known_pct=16.0).reason_codes == (
        'max_drawdown_at_least_15pct',)
    assert judged(waived, closed_trades=3, net_return_pct=5.0, max_drawdown_known_pct=2.0).status == 'pass'
    assert judged(strict, closed_trades=3, net_return_pct=5.0, max_drawdown_known_pct=2.0).reason_codes == (
        'minimum_6_closed_trades',)
    # A short series still waits whatever the waiver says.
    short = waived.model_copy(update={'history_bars': 399})
    assert 'minimum_400_anchored_h4_bars' in judged(short, closed_trades=0, net_return_pct=0.0,
                                                    max_drawdown_known_pct=0.0).reason_codes


def test_the_position_trail_reads_the_same_proxied_series_as_the_signal(tmp_path, series):
    from types import SimpleNamespace
    from intraday.contracts import SpotRuleParameters
    from intraday.execution.scoped_source import ScopedEvidenceSource
    store, boundary = build(tmp_path, series)
    large, _ = series
    now = large[-1].available_at+timedelta(minutes=1)
    source = ScopedEvidenceSource(store.database, symbol='HYPE', market='spot')
    source.rule = lambda: SimpleNamespace(parameters=SpotRuleParameters(entry_profile=PROFILE, entry_window=25,
                                                                       exit_window=10))
    entered = large[-60].opened_at
    entry = large[-60].close
    atr = D('2')
    got = source.setup2_trail(side=1, entry_price=entry, signal_atr=atr, entered_at=entered, now=now)
    settings = setup2_store.coin_settings(store, SYMBOL)
    bars = setup2_store.proxied_bars(store, SYMBOL, 'spot', '4h', since=FIRST, until=setup2.floor_h4(now),
                                     settings=settings)
    assert got == setup2.trail_stop(1, entry, atr, bars, entered_at=entered, anchor=FIRST, cap=D('.05'))
    # Raw Spot bars alone cannot anchor, so a trail without the proxy would never ratchet.
    raw = setup2_store.list_bars(store, SYMBOL, 'spot', '4h', since=FIRST, until=setup2.floor_h4(now))
    assert setup2.anchored(raw, FIRST) is None


def test_cli_builds_the_proxy_plan_and_refuses_the_settings_for_the_other_profile():
    import argparse
    from datetime import datetime, timezone
    from intraday.active_set_cli import build_plan
    now = datetime(2026, 10, 14, tzinfo=timezone.utc)

    def arguments(**kw):
        base = dict(coins=None, mode='both', coin=[], split=None, profile=PROFILE, watch=[], anchor='2025-06-01',
                    warmup=[], spot_proxy=[], waive_min_trades=[])
        return argparse.Namespace(**(base | kw))
    plan = build_plan(arguments(coin=['ETH:both', 'HYPE:both'], warmup=['HYPE=400'],
                                spot_proxy=['HYPE=2026-09-24T08:00:00Z'], waive_min_trades=['HYPE:spot']), None, now)
    hype, eth = plan.coins['HYPEUSDT'], plan.coins['ETHUSDT']
    assert (hype.warmup_bars, hype.waive_min_trades) == (400, ('spot',))
    assert hype.spot_price_proxy_until == datetime(2026, 9, 24, 8, tzinfo=timezone.utc)
    assert (eth.warmup_bars, eth.spot_price_proxy_until, eth.waive_min_trades) == (600, None, ())
    for bad in (dict(warmup=['SOL=400']), dict(waive_min_trades=['HYPE:perp']), dict(warmup=['HYPE=399'])):
        with pytest.raises(ValueError):
            build_plan(arguments(coin=['ETH:both', 'HYPE:both'], **bad), None, now)
    with pytest.raises(ValueError, match='slot profile'):
        build_plan(arguments(coins='ETH,NEAR,SOL', profile='setup2_v1', warmup=['ETH=400']), None, now)


def test_rotation_in_needs_the_proxy_history_and_twenty_days_of_the_markets_own_bars(tmp_path, series):
    store, boundary = build(tmp_path, series, own_bars=172)
    coin = active_set.current(store).plan.coins[SYMBOL]
    now = series[0][-1].available_at
    # 500 Perp bars before the boundary satisfy the 400-bar warm-up, but the asset-candle table holds no Spot bars
    # of the market's own, so the twenty days of its own volume and profile are still missing.
    blockers = active_set.rotation_blockers(store, SYMBOL, coin, now)
    assert 'spot_own_history_not_ready' in blockers and 'history_not_ready' not in blockers
    view = active_set.readiness_view(store, SYMBOL, 'spot', now)
    assert 'spot_own_history_not_ready' in view['rotation_labels'] and 'history_not_ready' not in view['rotation_labels']
    # Without the proxy the same coin would need its own 400 bars.
    bare = coin.model_copy(update={'spot_price_proxy_until': None, 'waive_min_trades': ()})
    assert 'history_not_ready' in active_set.rotation_blockers(store, SYMBOL, bare, now)


def test_cli_anchors_can_differ_per_coin():
    import argparse
    from datetime import datetime, timezone
    from intraday.active_set_cli import build_plan
    now = datetime(2026, 10, 14, tzinfo=timezone.utc)
    arguments = argparse.Namespace(coins=None, mode='both', coin=['ETH:both', 'HYPE:both'], split=None, profile=PROFILE,
                                   watch=[], anchor=['2024-01-01', 'HYPE=2025-06-01'], warmup=[], spot_proxy=[],
                                   waive_min_trades=[])
    plan = build_plan(arguments, None, now)
    assert plan.anchor('ETHUSDT') == datetime(2024, 1, 1, tzinfo=timezone.utc)
    assert plan.anchor('HYPEUSDT') == datetime(2025, 6, 1, tzinfo=timezone.utc)
    arguments.anchor = ['HYPE=2025-06-01', 'HYPE=2025-07-01']
    with pytest.raises(ValueError, match='one --anchor per coin'):
        build_plan(arguments, None, now)
