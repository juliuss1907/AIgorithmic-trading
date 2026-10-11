import json
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
