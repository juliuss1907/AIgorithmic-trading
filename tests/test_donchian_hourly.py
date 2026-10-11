import json
from pathlib import Path
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from typing import Literal

import pytest

from test_donchian_filter_engine import fixture
from intraday.replay_v2 import donchian_h1_verdict as verdict
from intraday.replay_v2.donchian_filter_book import FilterConfig
from intraday.replay_v2.donchian_filter_engine import prepare, simulate
from intraday.replay_v2.donchian_hourly import KNOWN_PERP_H4_DISAGREEMENTS, build, h4_reproduction, hourly_series
from intraday.replay_v2.intraday_timeframes import IntradayCandle

T0 = datetime(2024, 10, 29, tzinfo=timezone.utc)


class HourlyConfig(FilterConfig):
    bar_interval: Literal['1h'] = '1h'


def m15(count, *, at=T0, skip=()):
    """Distinct prices per bar so any wrong aggregation changes OHLCV."""
    rows = []
    for i in range(count):
        opened = at+timedelta(minutes=15*i)
        if opened in skip:
            continue
        base = D(100+i)
        rows.append(IntradayCandle(interval='15m', opened_at=opened, available_at=opened+timedelta(minutes=15),
                                   open=base, high=base+3, low=base-2, close=base+1, volume=D(i+1)))
    return tuple(rows)


def test_hour_is_first_open_extremes_last_close_and_summed_volume():
    bars, gaps = hourly_series(m15(8), T0, T0+timedelta(hours=2))
    assert len(bars) == 2 and gaps == dict(absent_hours=[], partial_hours=[])
    first = bars[0]
    assert (first.open, first.high, first.low, first.close, first.volume) == (D(100), D(106), D(98), D(104), D(10))
    assert bars[1].opened_at == T0+timedelta(hours=1) and bars[1].available_at == T0+timedelta(hours=2)
    assert (bars[1].open, bars[1].close, bars[1].volume) == (D(104), D(108), D(26))


def test_audited_gap_leaves_absent_and_partial_hours_never_synthetic_bars():
    missing = tuple(T0+timedelta(minutes=45+15*i) for i in range(5))  # 00:45 .. 01:45
    bars, gaps = hourly_series(m15(16, skip=set(missing)), T0, T0+timedelta(hours=4), missing_opens=missing)
    assert [b.opened_at.hour for b in bars] == [0, 2, 3]
    assert gaps == dict(absent_hours=[(T0+timedelta(hours=1)).isoformat()], partial_hours=[T0.isoformat()])
    assert bars[0].close == D(103)  # Last published quarter, not a filled one.


def test_unaudited_missing_quarter_stops_the_derivation():
    with pytest.raises(ValueError, match='unaudited M15 gap'):
        hourly_series(m15(8, skip={T0+timedelta(minutes=30)}), T0, T0+timedelta(hours=2))


def native_h4(small):
    from intraday.replay_v2.contracts import Candle
    bars = []
    for start in range(0, len(small), 16):
        members = small[start:start+16]
        bars.append(Candle(opened_at=members[0].opened_at, available_at=members[-1].available_at, open=members[0].open,
                           high=max(m.high for m in members), low=min(m.low for m in members),
                           close=members[-1].close, volume=sum((m.volume for m in members), D(0))))
    return tuple(bars)


def test_m15_aggregation_reproduces_native_h4_and_flags_a_changed_candle():
    small = m15(32)
    large = native_h4(small)
    assert h4_reproduction(large, small) == dict(compared=2, skipped_incomplete=0, disagreements=[])
    changed = (large[0], large[1].model_copy(update={'volume': D(1)}))
    assert h4_reproduction(changed, small)['disagreements'] == [large[1].opened_at]


def test_build_rejects_unknown_disagreements_but_tolerates_the_audited_perp_windows():
    cfg, data, _ = fixture()
    symbols = list(cfg.weights)
    hourly, derivation = build(data, symbols, cfg.start, cfg.end)
    assert set(hourly[symbols[0]]) == {'spot1h', 'perp1h', 'spot15m', 'perp15m', 'mark15m'}
    assert derivation[symbols[0]]['spot']['h4_disagreements'] == [] and derivation[symbols[0]]['spot']['synthetic_bars'] == 0
    large = list(data[symbols[0]]['perp4h'])
    large[-1] = large[-1].model_copy(update={'close': D(7)})
    data[symbols[0]]['perp4h'] = tuple(large)
    with pytest.raises(ValueError, match='disagrees with native H4'):
        build(data, symbols, cfg.start, cfg.end)
    assert datetime(2023, 11, 10, 12, tzinfo=timezone.utc) in KNOWN_PERP_H4_DISAGREEMENTS


def hourly_setup(days=1):
    cfg, data, funding = fixture(days=days)
    symbols = list(cfg.weights)
    hourly_cfg = HourlyConfig(start=cfg.start, end=cfg.end)
    hourly, derivation = build(data, symbols, cfg.start, cfg.end)
    return hourly_cfg, data, hourly, derivation, funding


def test_hourly_study_needs_an_audited_derivation():
    cfg, _, hourly, derivation, funding = hourly_setup()
    with pytest.raises(ValueError, match='audited M15 derivation'):
        prepare(cfg, hourly, funding)
    assert prepare(cfg, hourly, funding, derivation=derivation).bar_interval == '1h'


def test_profile_covers_120_hourly_bars_excluding_the_trigger_and_nothing_older():
    cfg, _, hourly, derivation, funding = hourly_setup()
    before = prepare(cfg, hourly, funding, derivation=derivation)
    at = cfg.start  # Bar [-1h, 0h) closes here; its profile is the 120 hours before -1h.
    base = before.features['spot']['BTCUSDT'][at]['profile']

    def altered(opened_at):
        series = {s: dict(tags) for s, tags in hourly.items()}
        rows = list(series['BTCUSDT']['spot15m'])
        index = next(i for i, b in enumerate(rows) if b.opened_at == opened_at)
        rows[index] = rows[index].model_copy(update={'volume': D(999999)})
        series['BTCUSDT']['spot15m'] = tuple(rows)
        return prepare(cfg, series, funding, derivation=derivation).features['spot']['BTCUSDT'][at]['profile']

    trigger_hour_start = cfg.start-timedelta(hours=1)
    assert altered(cfg.start) == base                                # future volume
    assert altered(trigger_hour_start) == base                       # the trigger bar itself
    assert altered(trigger_hour_start-timedelta(minutes=15)) != base  # last quarter inside the window
    assert altered(trigger_hour_start-timedelta(hours=120)) != base   # first quarter inside the window
    assert altered(trigger_hour_start-timedelta(hours=120, minutes=15)) == base  # just outside


def test_hourly_breakout_enters_and_labels_the_journal_as_h1():
    cfg, _, hourly, derivation, funding = hourly_setup()
    cut = cfg.start-timedelta(hours=1)
    for series in hourly.values():
        for tag in ('spot1h', 'spot15m'):
            series[tag] = tuple(
                b.model_copy(update={'open': D(130), 'high': D(131), 'low': D(129), 'close': D(130)})
                if b.opened_at >= cut else b for b in series[tag])
    prepared = prepare(cfg, hourly, funding, derivation=derivation)
    report = simulate(cfg, prepared)
    entries = [e for e in report['events'] if e['kind'] == 'entry_features']
    assert entries and {e['reason'] for e in entries} == {'closed_h1'}
    assert report['inputs']['signal_bar_derivation']['BTCUSDT']['spot']['hourly_bars'] > 0
    assert 'H1 signal next H1 open' in report['methodology']['fills']
    assert 'hourly_bars_derived_from_native_m15_not_native_h1' in report['limitations']


def test_default_h4_report_is_untouched_by_the_hourly_option():
    cfg, data, funding = fixture()
    report = simulate(cfg, prepare(cfg, data, funding))
    assert 'signal_bar_derivation' not in report['inputs']
    assert 'bar_interval' not in report['config']
    assert report['methodology']['signals'].startswith('closed native H4')
    assert not any(e['reason'].startswith('closed_h1') for e in report['events'] if 'reason' in e)
    assert 'hourly_bars_derived_from_native_m15_not_native_h1' not in report['limitations']


def receipt(tmp_path, *, h1_return=90.0, h1_dd=10.0, cost_return=60.0, perp=50.0, after_split=-5.0):
    def item(name, *, return_pct, dd, final=None, contributions=None, fees=(10.0, 5.0), trades=100):
        directory = tmp_path/name
        directory.mkdir()
        rows = [dict(at=T0.replace(year=2022, month=1).isoformat(), equity_known='1000'),
                dict(at=(verdict.SPLIT-timedelta(minutes=15)).isoformat(), equity_known='1500'),
                dict(at=(verdict.SPLIT+timedelta(days=30)).isoformat(), equity_known=str(1500+after_split))]
        (directory/'equity_curve.jsonl').write_text('\n'.join(json.dumps(r) for r in rows))
        return dict(variant=name, report_directory=str(directory), summary=dict(
            initial_capital=1000.0, final_equity_known=final if final is not None else 1000*(1+return_pct/100),
            net_return_pct=return_pct, max_drawdown_known_pct=dd, closed_trades=trades,
            exchange_fee_known=fees[0], slippage_cost_known=fees[1],
            contributions=contributions or {'spot:ETHUSDT': dict(net_pnl=100.0), 'perp:ETHUSDT': dict(net_pnl=perp)}))
    return dict(results=[
        item('ETH-NEAR-SOL-H4', return_pct=80.0, dd=11.0),
        item('ETH-NEAR-SOL-H4-cost2x', return_pct=57.0, dd=12.0),
        item('ETH-NEAR-SOL-H1', return_pct=h1_return, dd=h1_dd, final=1500+after_split),
        item('ETH-NEAR-SOL-H1-cost2x', return_pct=cost_return, dd=h1_dd+1)])


def test_verdict_advances_only_when_every_registered_criterion_passes(tmp_path):
    result = verdict.evaluate(receipt(tmp_path))
    assert result['advance'] is False  # The second half loses in this fixture.
    assert [c['name'] for c in result['criteria'] if not c['passed']] == ['both_halves_positive']


@pytest.mark.parametrize('overrides, failing', [
    (dict(after_split=250.0), None),
    (dict(after_split=250.0, h1_return=70.0), 'net_return_not_below_h4_control'),
    (dict(after_split=250.0, h1_dd=15.0), 'max_drawdown_below_15pct'),
    (dict(after_split=250.0, cost_return=-1.0), 'doubled_costs_still_positive'),
    (dict(after_split=250.0, perp=-0.5), 'spot_and_perp_net_positive'),
])
def test_each_registered_criterion_blocks_alone(tmp_path, overrides, failing):
    result = verdict.evaluate(receipt(tmp_path, **overrides))
    assert [c['name'] for c in result['criteria'] if not c['passed']] == ([failing] if failing else [])
    assert result['advance'] is (failing is None)


# --- Second round: one-concept H1 candidates --------------------------------------------------

from pydantic import ValidationError  # noqa: E402

from intraday.replay_v2.contracts import Candle  # noqa: E402
from intraday.replay_v2.donchian_adx_setups import (  # noqa: E402
    BasketConfig, H1Adx30Config, H1BasketConfig, basket_specification, h1_family_cases)
from intraday.replay_v2.donchian_filter_book import ATRTrail  # noqa: E402
from intraday.replay_v2.donchian_filters import entry_filters, indicator_series  # noqa: E402


class RegimeConfig(HourlyConfig):
    regime_interval: Literal['4h'] = '4h'


class GeometryConfig(HourlyConfig):
    stop_atr_multiple: Literal[6] = 6


class FastEmaConfig(HourlyConfig):
    ema_fast: Literal[20] = 20
    ema_slow: Literal[50] = 50


def closes(values):
    return [Candle(opened_at=T0+timedelta(hours=4*i), available_at=T0+timedelta(hours=4*i+4),
                   open=D(v), high=D(v)+1, low=D(v)-1, close=D(v), volume=D(10)) for i, v in enumerate(values)]


def test_atr_trail_multiple_defaults_to_three_and_scales_the_stop_distance():
    assert ATRTrail(1, D(100), D(2)).stop == D(94)
    wide = ATRTrail(1, D(100), D(2), 6)
    assert wide.stop == D(88)
    wide.update(D(120), D(110), D(2))
    assert wide.stop == D(108)  # extreme 120 - 6 * 2; never loosens


def test_indicator_pair_changes_seeds_and_names_without_touching_the_legacy_keys():
    rows = closes(range(100, 160))
    legacy = indicator_series(rows)[60-1]
    assert {'ema50', 'prior_ema50', 'ema200'} <= set(legacy) and legacy['ema200'] is None
    fast = indicator_series(rows, ema_fast=20, ema_slow=50)
    assert 'ema50' not in fast[0] and {'ema_fast', 'prior_ema_fast', 'ema_slow'} <= set(fast[0])
    assert fast[18]['ema_fast'] is None and fast[19]['ema_fast'] == sum(D(100+i) for i in range(20))/20
    assert fast[48]['ema_slow'] is None and fast[49]['ema_slow'] == sum(D(100+i) for i in range(50))/50
    assert fast[20]['ema_fast'] == fast[19]['ema_fast']+D(2)/21*(D(120)-fast[19]['ema_fast'])
    assert fast[20]['prior_ema_fast'] == fast[19]['ema_fast']


def test_role_named_filters_block_on_the_right_side_and_slope():
    ok = dict(close=D(110), ema_slow=D(100), ema_fast=D(105), prior_ema_fast=D(104), adx=D(30), prior_adx=D(25),
              plus_di=D(30), minus_di=D(10), volume=D(5), volume_ma=D(1), profile=dict(vah=100, val=90))
    assert entry_filters(ok, 1, 4, adx_threshold=20, ema_fast=20, ema_slow=50) == []
    assert entry_filters({**ok, 'ema_fast': D(115)}, 1, 1, ema_fast=20, ema_slow=50) == ['ema_fast']
    assert entry_filters({**ok, 'prior_ema_fast': D(106)}, 1, 1, ema_fast=20, ema_slow=50) == ['ema_fast']
    assert entry_filters({**ok, 'ema_slow': D(120)}, 1, 1, ema_fast=20, ema_slow=50) == ['ema_slow']
    assert entry_filters({**ok, 'adx': D(25)}, 1, 2, adx_threshold=30, ema_fast=20, ema_slow=50) == ['adx']


def test_prepared_features_must_match_the_study_indicator_settings():
    cfg, data, hourly, derivation, funding = hourly_setup()
    prepared = prepare(cfg, hourly, funding, derivation=derivation)
    for other in (FastEmaConfig(start=cfg.start, end=cfg.end),):
        with pytest.raises(ValueError, match='another indicator configuration'):
            simulate(other, prepared)
    assert prepared.ema_periods == (50, 200) and prepared.regime_interval is None
    fast = prepare(FastEmaConfig(start=cfg.start, end=cfg.end), hourly, funding, derivation=derivation)
    assert fast.ema_periods == (20, 50) and fast.checksum != prepared.checksum
    assert {'ema_fast', 'ema_slow'} <= set(fast.features['spot']['BTCUSDT'][cfg.start])


def regime_setup():
    cfg, data, funding = fixture()
    symbols = list(cfg.weights)
    hourly, derivation = build(data, symbols, cfg.start, cfg.end, keep_h4=True)
    return RegimeConfig(start=cfg.start, end=cfg.end), data, hourly, derivation, funding


def test_regime_gate_reads_only_h4_bars_already_closed_at_the_h1_close():
    cfg, _, hourly, derivation, funding = regime_setup()
    base = prepare(cfg, hourly, funding, derivation=derivation).features['spot']['BTCUSDT']
    assert base[cfg.start]['regime_available_at'] == cfg.start.isoformat()
    assert base[cfg.start+timedelta(hours=3)]['regime_available_at'] == cfg.start.isoformat()
    assert base[cfg.start+timedelta(hours=4)]['regime_available_at'] == (cfg.start+timedelta(hours=4)).isoformat()

    series = {s: dict(tags) for s, tags in hourly.items()}
    rows = list(series['BTCUSDT']['spot4h'])
    index = next(i for i, b in enumerate(rows) if b.opened_at == cfg.start)
    rows[index] = rows[index].model_copy(update={'close': D(500), 'high': D(501)})
    series['BTCUSDT']['spot4h'] = tuple(rows)
    changed = prepare(cfg, series, funding, derivation=derivation).features['spot']['BTCUSDT']
    for hours in range(0, 4):  # The altered H4 bar is still open until START+4h.
        at = cfg.start+timedelta(hours=hours)
        assert changed[at]['ema50'] == base[at]['ema50'] and changed[at]['adx'] == base[at]['adx']
    assert changed[cfg.start+timedelta(hours=4)]['ema50'] != base[cfg.start+timedelta(hours=4)]['ema50']


def test_regime_gate_needs_the_default_pair_and_h1_bars():
    cfg, _, hourly, derivation, funding = regime_setup()

    class Mixed(RegimeConfig):
        ema_fast: Literal[20] = 20
        ema_slow: Literal[50] = 50
    with pytest.raises(ValueError, match='H4 regime gate'):
        prepare(Mixed(start=cfg.start, end=cfg.end), hourly, funding, derivation=derivation)


def breakout_setup(config_type):
    cfg, _, hourly, derivation, funding = hourly_setup()
    cut = cfg.start-timedelta(hours=1)
    for series in hourly.values():
        for tag in ('spot1h', 'spot15m'):
            series[tag] = tuple(
                b.model_copy(update={'open': D(130), 'high': D(131), 'low': D(129), 'close': D(130)})
                if b.opened_at >= cut else b for b in series[tag])
    config = config_type(start=cfg.start, end=cfg.end)
    return config, prepare(config, hourly, funding, derivation=derivation)


def test_wider_stop_multiple_doubles_the_initial_stop_distance_and_is_reported():
    base_cfg, base_prepared = breakout_setup(HourlyConfig)
    wide_cfg, wide_prepared = breakout_setup(GeometryConfig)

    def first_stop(report):
        entry = next(e for e in report['events'] if e['kind'] == 'entry_features' and e['symbol'] == 'BTCUSDT')
        return D(entry['stop']), D(next(t for t in report['trades'] if t['symbol'] == 'BTCUSDT'
                                         and t['market'] == 'spot')['entry_price'])
    base, base_price = first_stop(simulate(base_cfg, base_prepared))
    report = simulate(wide_cfg, wide_prepared)
    wide, wide_price = first_stop(report)
    assert base_price == wide_price and wide_price-wide == 2*(base_price-base)
    assert report['methodology']['variant'] == {'stop_atr_multiple': 6}


def test_default_reports_carry_no_variant_block():
    cfg, prepared = breakout_setup(HourlyConfig)
    assert 'variant' not in simulate(cfg, prepared)['methodology']


def test_family_configs_change_one_concept_each_and_leave_the_baseline_payload_alone():
    from datetime import datetime as dt
    start, end = dt(2022, 1, 1, tzinfo=timezone.utc), dt(2026, 10, 2, tzinfo=timezone.utc)
    cases = dict(h1_family_cases(start, end))
    assert list(cases)[:4] == ['ETH-NEAR-SOL-H4', 'ETH-NEAR-SOL-H4-cost2x', 'ETH-NEAR-SOL-H1', 'ETH-NEAR-SOL-H1-cost2x']
    assert len(cases) == 12
    baseline = cases['ETH-NEAR-SOL-H1'].model_dump(mode='json')
    assert baseline['bar_interval'] == '1h' and not {'stop_atr_multiple', 'ema_fast', 'ema_slow', 'regime_interval'} & set(baseline)
    assert baseline['exit_window'] == 10 and baseline['adx_by_market']['spot']['ETHUSDT'] == 20
    geometry = cases['ETH-NEAR-SOL-H1-GEOM'].model_dump(mode='json')
    assert (geometry['stop_atr_multiple'], geometry['exit_window']) == (6, 40)
    assert cases['ETH-NEAR-SOL-H1-ADX30'].model_dump(mode='json')['adx_by_market']['perp']['SOLUSDT'] == 30
    assert cases['ETH-NEAR-SOL-H1-REGIME'].model_dump(mode='json')['regime_interval'] == '4h'
    fast = cases['ETH-NEAR-SOL-H1-EMA50-20'].model_dump(mode='json')
    assert (fast['ema_fast'], fast['ema_slow']) == (20, 50)
    assert all(cases[f'{n}-cost2x'].model_dump(mode='json')['cost_multiplier'] == 2 for n in
               ('ETH-NEAR-SOL-H4', 'ETH-NEAR-SOL-H1-GEOM', 'ETH-NEAR-SOL-H1-REGIME'))


def test_adx30_is_only_accepted_by_its_own_candidate():
    from datetime import datetime as dt
    start, end = dt(2022, 1, 1, tzinfo=timezone.utc), dt(2026, 10, 2, tzinfo=timezone.utc)
    spec = basket_specification(('ETH', 'NEAR', 'SOL'))
    thirty = {m: dict.fromkeys(spec['weights'], 30) for m in ('spot', 'perp')}
    H1Adx30Config(start=start, end=end, basket=('ETH', 'NEAR', 'SOL'), **{**spec, 'adx_by_market': thirty})
    with pytest.raises(ValidationError):
        H1BasketConfig(start=start, end=end, basket=('ETH', 'NEAR', 'SOL'), **{**spec, 'adx_by_market': thirty})
    with pytest.raises(ValidationError):
        BasketConfig(start=start, end=end, basket=('ETH', 'NEAR', 'SOL'), **{**spec, 'adx_by_market': thirty})


def test_verdict_family_reads_each_candidate_against_the_same_control(tmp_path):
    rec = receipt(tmp_path, after_split=250.0)
    directory = tmp_path/'ETH-NEAR-SOL-H1'
    trades = [dict(opened_at='2023-01-01T00:00:00+00:00', closed_at='2023-01-04T00:00:00+00:00', gross_pnl='30'),
              dict(opened_at='2023-02-01T00:00:00+00:00', closed_at='2023-02-01T06:00:00+00:00', gross_pnl='-2')]
    for item in rec['results']:
        Path(item['report_directory']).joinpath('trades.jsonl').write_text('\n'.join(json.dumps(t) for t in trades))
    for extra in ('', '-cost2x'):
        twin = json.loads(json.dumps(rec['results'][3 if extra else 2]))
        twin['variant'] = f'ETH-NEAR-SOL-H1-GEOM{extra}'
        rec['results'].append(twin)
    result = verdict.evaluate(rec, 'H1-GEOM')
    assert result['candidate'] == 'H1-GEOM' and result['advance'] is True
    assert verdict.hold_profile(directory) == dict(trades=2, median_hold_hours=72.0, holds_over_48h=1,
                                                   gross_pnl_over_48h=30.0)


def test_window_start_keeps_only_warmup_and_the_new_window_and_prepares_cleanly():
    from intraday.replay_v2.donchian_adx_study import window_data
    cfg, data, funding = fixture(days=3)
    later = cfg.start+timedelta(days=1)
    sliced = window_data(data, later)
    symbol = next(iter(sliced))
    assert sliced[symbol]['perp4h'][0].opened_at == later-timedelta(hours=2400)
    assert sliced[symbol]['spot15m'][0].opened_at == later-timedelta(hours=484)
    assert sliced[symbol]['mark15m'][0].opened_at == later
    shorter = FilterConfig(start=later, end=cfg.end)
    prepared = prepare(shorter, sliced, funding)
    assert min(prepared.features['spot'][symbol]) == later
    assert simulate(shorter, prepared)['summary']['closed_trades'] >= 0


def test_verdict_split_is_the_registered_date_or_the_window_midpoint():
    from datetime import datetime as dt
    full = {'window': {'start': '2022-01-01T00:00:00+00:00', 'end': '2026-10-02T00:00:00+00:00'}}
    short = {'window': {'start': '2026-01-01T00:00:00+00:00', 'end': '2026-10-02T00:00:00+00:00'}}
    assert verdict.split_for(full) == verdict.SPLIT
    assert verdict.split_for(short) == dt(2026, 5, 18, tzinfo=timezone.utc)
