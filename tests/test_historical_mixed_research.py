from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from intraday.replay_v2.contracts import Candle


START = datetime(2026, 9, 30, tzinfo=timezone.utc)
WIDTH = timedelta(hours=4)


def bars(start, count, price=100):
    return tuple(Candle(opened_at=start+i*WIDTH, available_at=start+(i+1)*WIDTH,
        open=price, high=Decimal(price)+1, low=Decimal(price)-1,
        close=price, volume=1) for i in range(count))


def daily_rows(start, count, descending=False):
    rows = []
    for i in range(count):
        at = int((start+timedelta(days=i)).timestamp()*1000)
        price = 200-i if descending else 100+i
        rows.append([at, price, price+1, price-1, price, 1, at+86400000-1])
    return rows


def test_historical_pagination_freezes_exact_native_bars():
    from intraday.replay_v2.historical_data import fetch_candle_snapshot

    expected = bars(START, 5)
    queries = []
    def fetch(path, query):
        queries.append((path, query))
        return [c.row() for c in expected if query['startTime'] <= int(c.opened_at.timestamp()*1000)][:2]
    snapshot = fetch_candle_snapshot('BTCUSDT', '4h', START, START+5*WIDTH,
                                     fetch_json=fetch, page_size=2, now=START+6*WIDTH)
    assert snapshot.candles() == expected
    assert len(queries) == 3
    assert all(path == '/fapi/v1/klines' for path, _ in queries)
    assert snapshot.source.endswith('/fapi/v1/klines')
    changed = snapshot.model_dump()
    changed['raw_rows'] = [list(row) for row in changed['raw_rows']]
    changed['raw_rows'][0][4] = 99
    with pytest.raises(ValueError, match='checksum'):
        type(snapshot).model_validate(changed)


@pytest.mark.parametrize('kind', ['gap', 'duplicate', 'wrong_width', 'nan'])
def test_historical_collection_refuses_bad_or_incomplete_coverage(kind):
    from intraday.replay_v2.historical_data import fetch_candle_snapshot

    rows = [c.row() for c in bars(START, 3)]
    if kind == 'gap':
        rows.pop(1)
    elif kind == 'duplicate':
        rows[1] = rows[0]
    elif kind == 'wrong_width':
        rows[0][6] -= 1
    else:
        rows[0][4] = 'NaN'
    with pytest.raises(ValueError):
        fetch_candle_snapshot('BTCUSDT', '4h', START, START+3*WIDTH,
            fetch_json=lambda path, query: rows, now=START+4*WIDTH)


def test_perp_signal_long_short_mirror_and_no_current_bar_in_channel():
    from intraday.replay_v2.historical_mixed import perp_observation

    rows = [c.row() for c in bars(START-31*WIDTH, 31)]
    rows[-1][2] = rows[-1][4] = 105
    obs = perp_observation(rows)
    assert obs.entry_side == 1 and not obs.exit_long
    rows[-1][2] = 101
    rows[-1][3] = rows[-1][4] = 95
    obs = perp_observation(rows)
    assert obs.entry_side == -1 and obs.exit_long and not obs.exit_short


def test_daily_long_short_filter_only_uses_closed_daily_bars():
    from intraday.replay_v2.historical_mixed import daily_directions

    rows = daily_rows(START-timedelta(days=60), 62)
    times, sides = daily_directions(rows)
    assert sides[49] == 0 and sides[50] == 1
    assert times[59] == START
    _, short = daily_directions(daily_rows(START-timedelta(days=60), 62, True))
    assert short[50] == -1


def test_historical_config_and_stop_distance_are_explicit():
    from intraday.replay_v2.historical_mixed import HistoricalConfig, stop_fraction

    fixed = HistoricalConfig(start=START, end=START+WIDTH, perp_stop='fixed-1pct')
    adaptive = HistoricalConfig(start=START, end=START+WIDTH, perp_stop='atr14-2x')
    assert stop_fraction(fixed, Decimal(100), Decimal(2)) == Decimal('.01')
    assert stop_fraction(adaptive, Decimal(100), Decimal(2)) == Decimal('.04')
    assert fixed.trend_filter and fixed.exit_window == 8
    with pytest.raises(ValueError):
        HistoricalConfig(start=START, end=START+WIDTH, perp_stop='unknown')
    with pytest.raises(ValueError):
        HistoricalConfig(start=START, end=START+WIDTH, trend_filter=False, exit_window=6)


def test_historical_config_canonicalizes_allocation_order_for_decimal_replay():
    import json
    from intraday.replay_v2.historical_mixed import HistoricalConfig

    cfg = HistoricalConfig(start=START, end=START+WIDTH)
    restored = HistoricalConfig.model_validate(json.loads(json.dumps(cfg.model_dump(mode='json'), sort_keys=True)))
    assert list(cfg.weights) == list(restored.weights) == sorted(cfg.weights)
    assert list(cfg.perp_weights) == list(restored.perp_weights) == sorted(cfg.perp_weights)


def fixture_inputs(config, side=1, *, price_kind_offset=0):
    from intraday.replay_v2.contracts import FundingHistory, FundingSettlement

    warmup = list(bars(config.start-31*WIDTH, 31))
    last = warmup[-1]
    warmup[-1] = last.model_copy(update={'high': Decimal(105) if side == 1 else last.high,
        'low': Decimal(95) if side == -1 else last.low, 'close': Decimal(105 if side == 1 else 95)})
    count = int((config.end-config.start)/WIDTH)
    trade = tuple(warmup)+bars(config.start, count)
    spot = {s: bars(config.start-31*WIDTH, 31+count) for s in config.weights}
    perp = {s: {'trade': trade, 'mark': bars(config.start, count, 100+price_kind_offset), 'daily': ()}
            for s in config.perp_weights}
    funding = {s: FundingHistory(symbol=s, source='fixture', coverage_start=config.start,
        coverage_end=config.end, settlements=tuple(FundingSettlement(at=at, rate='.001', mark=100)
            for at in (config.start, config.start+WIDTH) if at < config.end)) for s in config.perp_weights}
    return spot, {}, perp, funding


@pytest.mark.parametrize('side', [1, -1])
@pytest.mark.parametrize('stop', ['fixed-1pct', 'atr14-2x'])
def test_historical_entries_are_native_next_open_and_pnl_reconciles(side, stop):
    from intraday.replay_v2.historical_mixed import HistoricalConfig, simulate_historical

    cfg = HistoricalConfig(start=START, end=START+2*WIDTH, trend_filter=False, perp_stop=stop)
    report = simulate_historical(cfg, *fixture_inputs(cfg, side))
    entries = [e for e in report['events'] if e['kind'] == 'entry']
    assert len(entries) == 2 and all(e['at'] == START.isoformat() for e in entries)
    assert all(e['reason'] == 'historical_donchian' and e['price'] == '100' for e in entries)
    assert all(t['side'] == ('long' if side == 1 else 'short') for t in report['trades'])
    assert report['summary']['final_equity_known'] == pytest.approx(1000+sum(float(t['net_pnl']) for t in report['trades']))
    assert not report['activation_allowed'] and not report['methodology']['official_gate_eligible']
    assert 'confidence_threshold' not in report['config']


def test_futures_mark_cannot_be_used_as_entry_fill_and_controls_ignore_perp():
    from intraday.replay_v2.historical_mixed import HistoricalConfig, simulate_historical

    cfg = HistoricalConfig(start=START, end=START+WIDTH, trend_filter=False, perp_stop='atr14-2x')
    inputs = fixture_inputs(cfg, price_kind_offset=2)
    report = simulate_historical(cfg, *inputs)
    assert all(e['price'] == '100' for e in report['events'] if e['kind'] == 'entry')
    control = simulate_historical(cfg.model_copy(update={'include_perp': False}), *inputs)
    assert not control['trades'] and control['summary']['net_return_pct'] == 0
    assert report['inputs']['dataset_checksum'] == control['inputs']['dataset_checksum']


def test_missing_funding_does_not_turn_into_zero_cost_full_return():
    from intraday.replay_v2.historical_mixed import HistoricalConfig, simulate_historical

    cfg = HistoricalConfig(start=START, end=START+WIDTH, trend_filter=False)
    spot, daily, perp, _ = fixture_inputs(cfg)
    report = simulate_historical(cfg, spot, daily, perp, {})
    assert report['summary']['net_return_pct'] is None
    assert not report['summary']['funding_complete']


def test_perp_bar_coverage_cannot_silently_shorten_window():
    from intraday.replay_v2.historical_mixed import HistoricalConfig, simulate_historical

    cfg = HistoricalConfig(start=START, end=START+2*WIDTH, trend_filter=False)
    spot, daily, perp, funding = fixture_inputs(cfg)
    perp['BTCUSDT']['mark'] = perp['BTCUSDT']['mark'][:-1]
    with pytest.raises(ValueError, match='mark'):
        simulate_historical(cfg, spot, daily, perp, funding)


def test_stop_touch_uses_contract_not_mark_and_no_same_boundary_reentry():
    from intraday.replay_v2.contracts import FundingSettlement
    from intraday.replay_v2.historical_mixed import HistoricalConfig, simulate_historical

    cfg = HistoricalConfig(start=START, end=START+2*WIDTH, trend_filter=False)
    spot, daily, perp, funding = fixture_inputs(cfg)
    for s in funding:
        funding[s] = funding[s].model_copy(update={'settlements': (
            FundingSettlement(at=START+timedelta(hours=1), rate='.001', mark=100),)})
    report = simulate_historical(cfg, spot, daily, perp, funding)
    assert all(t['exit_reason'] == 'contract_stop_detected_at_close' for t in report['trades'])
    assert all(t['exit_price'] == '99.00' for t in report['trades'])
    assert len([e for e in report['events'] if e['kind'] == 'entry']) == 2
    assert report['summary']['funding_paid_known'] > 0  # settlement before stop detection
    for s in perp:
        perp[s]['trade'] = tuple(c.model_copy(update={'low': Decimal('99.5')})
            if c.opened_at >= START else c for c in perp[s]['trade'])
        perp[s]['mark'] = tuple(c.model_copy(update={'low': Decimal(80)}) for c in perp[s]['mark'])
    untouched = simulate_historical(cfg, spot, daily, perp, funding)
    assert all(t['exit_reason'] != 'contract_stop_detected_at_close' for t in untouched['trades'])


def test_daily_loss_and_dd_flatten_next_open_not_at_funding_tick():
    from intraday.replay_v2.contracts import FundingSettlement
    from intraday.replay_v2.historical_mixed import HistoricalConfig, simulate_historical

    cfg = HistoricalConfig(start=START, end=START+2*WIDTH, trend_filter=False, perp_stop='atr14-2x')
    spot, daily, perp, funding = fixture_inputs(cfg)
    at = START+timedelta(hours=1)
    for s in funding:
        funding[s] = funding[s].model_copy(update={'settlements': (
            FundingSettlement(at=START, rate=0, mark=100), FundingSettlement(at=at, rate='.12', mark=100),
            FundingSettlement(at=START+WIDTH, rate=0, mark=100))})
    report = simulate_historical(cfg, spot, daily, perp, funding)
    assert report['summary']['daily_pause_count'] == 1
    assert all(t['closed_at'] == (START+WIDTH).isoformat() for t in report['trades'])
    assert all(t['exit_reason'] == 'daily_loss_limit' for t in report['trades'])
    terminal = simulate_historical(cfg.model_copy(update={'max_drawdown': Decimal('.03')}), spot, daily, perp, funding)
    assert terminal['summary']['halt_reason'] == 'max_drawdown'


def test_close_loss_before_midnight_pauses_old_day_and_resumes_after_flatten():
    from intraday.replay_v2.historical_mixed import HistoricalConfig, simulate_historical

    cfg = HistoricalConfig(start=START, end=START+timedelta(days=1, hours=4),
                           trend_filter=False, perp_stop='atr14-2x')
    spot, daily, perp, _ = fixture_inputs(cfg)
    for s in perp:
        perp[s]['mark'] = tuple(c.model_copy(update={'close': Decimal(90), 'low': Decimal(89)})
            if c.opened_at == START+timedelta(hours=20) else c for c in perp[s]['mark'])
    report = simulate_historical(cfg, spot, daily, perp, {})
    assert report['summary']['daily_pause_count'] == 1
    assert report['summary']['daily_resume_count'] == 1
    assert not report['summary']['halted']
    assert all(t['closed_at'] == (START+timedelta(days=1)).isoformat() for t in report['trades'])


def test_same_time_funding_is_atomic_for_opposite_positions():
    from intraday.replay_v2.contracts import FundingSettlement
    from intraday.replay_v2.historical_mixed import HistoricalConfig, simulate_historical

    cfg = HistoricalConfig(start=START, end=START+WIDTH, trend_filter=False,
                           perp_stop='atr14-2x', daily_loss='.01')
    spot, daily, perp, funding = fixture_inputs(cfg)
    *_, shorts, _ = fixture_inputs(cfg, side=-1)
    perp['ETHUSDT'] = shorts['ETHUSDT']
    for s in funding:
        funding[s] = funding[s].model_copy(update={'settlements': (
            FundingSettlement(at=START+timedelta(hours=1), rate='.1', mark=100),)})
    report = simulate_historical(cfg, spot, daily, perp, funding)
    assert not report['summary']['halted']
    assert report['summary']['daily_pause_count'] == 0
    assert 0 < report['summary']['funding_paid_known'] < .01


def test_future_bar_cannot_change_initial_signal_or_allocation():
    from intraday.replay_v2.historical_mixed import HistoricalConfig, simulate_historical

    cfg = HistoricalConfig(start=START, end=START+2*WIDTH, trend_filter=False, perp_stop='atr14-2x')
    spot, daily, perp, funding = fixture_inputs(cfg)
    original = simulate_historical(cfg, spot, daily, perp, funding)
    for s in perp:
        perp[s]['trade'] = tuple(c.model_copy(update={'high': Decimal(500), 'close': Decimal(500)})
            if c.opened_at == START+WIDTH else c for c in perp[s]['trade'])
    changed = simulate_historical(cfg, spot, daily, perp, funding)
    entry = lambda r: [e for e in r['events'] if e['kind'] == 'entry' and e['at'] == START.isoformat()]
    assert entry(original) == entry(changed)


def test_stop_gap_uses_contract_open_not_unavailable_stop_fill():
    from intraday.replay_v2.historical_mixed import HistoricalConfig, simulate_historical

    cfg = HistoricalConfig(start=START, end=START+2*WIDTH, trend_filter=False)
    spot, daily, perp, funding = fixture_inputs(cfg)
    for s in perp:
        perp[s]['trade'] = tuple(c.model_copy(update={'low': Decimal('99.5')}) if c.opened_at == START else
            c.model_copy(update={'open': Decimal(98), 'low': Decimal(97)}) if c.opened_at == START+WIDTH else c
            for c in perp[s]['trade'])
    report = simulate_historical(cfg, spot, daily, perp, funding)
    assert all(t['exit_price'] == '98' and t['exit_reason'] == 'contract_stop_gap' for t in report['trades'])


def test_exhausted_isolated_collateral_invalidates_full_return():
    from intraday.replay_v2.historical_mixed import HistoricalConfig, simulate_historical

    cfg = HistoricalConfig(start=START, end=START+2*WIDTH, trend_filter=False, perp_stop='atr14-2x')
    spot, daily, perp, funding = fixture_inputs(cfg)
    for s in perp:
        perp[s]['mark'] = tuple(c.model_copy(update={'close': Decimal(60), 'low': Decimal(59)}) for c in perp[s]['mark'])
    report = simulate_historical(cfg, spot, daily, perp, funding)
    assert 'unsupported_liquidation' in report['limitations']
    assert report['summary']['net_return_pct'] is None


def test_missing_funding_settlement_gap_is_reported_not_interpolated():
    from intraday.replay_v2.historical_mixed import HistoricalConfig, funding_audit

    cfg = HistoricalConfig(start=START, end=START+4*WIDTH, trend_filter=False)
    *_, funding = fixture_inputs(cfg)
    assert not funding_audit(cfg, funding)['BTCUSDT']['complete']


def test_funding_audit_accepts_millisecond_jitter_but_keeps_actual_times():
    from intraday.replay_v2.contracts import FundingSettlement
    from intraday.replay_v2.historical_mixed import HistoricalConfig, funding_audit

    cfg = HistoricalConfig(start=START, end=START+3*WIDTH, trend_filter=False)
    *_, funding = fixture_inputs(cfg)
    jittered = START+timedelta(hours=8, milliseconds=16)
    for s in funding:
        funding[s] = funding[s].model_copy(update={'settlements': (
            FundingSettlement(at=START, rate='.001', mark=100),
            FundingSettlement(at=jittered, rate='.001', mark=100))})
    audit = funding_audit(cfg, funding)
    assert all(item['complete'] for item in audit.values())
    assert audit['BTCUSDT']['maximum_interval_seconds'] == 28800.016
    assert funding['BTCUSDT'].settlements[-1].at == jittered


def test_historical_study_runs_six_configs_and_reproduces_all_journals(tmp_path):
    from intraday.backups import create_backup
    from intraday.store import IntradayStore
    from intraday.replay_v2.historical_mixed import HistoricalConfig
    from intraday.replay_v2.historical_study import run_study

    source = create_backup(IntradayStore(tmp_path/'source.sqlite3').database, tmp_path/'evidence')
    cfg = HistoricalConfig(start=START, end=START+WIDTH, trend_filter=False)
    spot, daily, perp, funding = fixture_inputs(cfg)
    receipt = run_study(source['backup'], tmp_path/'reports', start=cfg.start, end=cfg.end,
        loader=lambda store, config: (spot, daily), perp_inputs=(perp, funding), trend_filter=False)
    assert len(receipt['results']) == 6
    assert len({r['result_id'] for r in receipt['results']}) == 6
    assert len({r['dataset_checksum'] for r in receipt['results']}) == 1
    assert all(r['deterministic_rerun_verified'] for r in receipt['results'])
    assert receipt['source_unchanged'] and not receipt['activation_allowed']
    assert (tmp_path/'reports'/'spot-inputs.json').exists()
    with pytest.raises(FileExistsError):
        run_study(source['backup'], tmp_path/'reports', start=cfg.start, end=cfg.end,
            loader=lambda store, config: (spot, daily), perp_inputs=(perp, funding), trend_filter=False)


def test_cli_routes_historical_mode_without_touching_recorded_jev(monkeypatch):
    from intraday.replay_v2 import mixed_portfolio_research as cli
    from intraday.replay_v2 import historical_study

    calls = []
    monkeypatch.setattr(historical_study, 'run_study', lambda *args, **kwargs: calls.append(kwargs) or
        {'results': [None]*6, 'source_unchanged': True})
    monkeypatch.setattr(cli, 'run_study', lambda *args, **kwargs: pytest.fail('must not load Jev'))
    cli.main(['--mode', 'historical-quant', '--database', 'backup', '--report-root', 'new', '--collect-perp'])
    assert len(calls) == 1 and calls[0]['collect_perp']


def test_cli_default_still_routes_recorded_jev(monkeypatch):
    from intraday.replay_v2 import mixed_portfolio_research as cli

    calls = []
    monkeypatch.setattr(cli, 'run_study', lambda *args, **kwargs: calls.append(kwargs) or
        {'results': [None]*4, 'source_unchanged': True})
    cli.main(['--database', 'backup', '--report-root', 'new'])
    assert len(calls) == 1 and not calls[0]['collect_funding']


@pytest.mark.parametrize('arguments', [[], ['--collect-perp', '--perp-inputs', 'x'], ['--collect-funding']])
def test_cli_rejects_ambiguous_historical_collection(arguments):
    from intraday.replay_v2.mixed_portfolio_research import main

    with pytest.raises(SystemExit) as exc:
        main(['--mode', 'historical-quant', '--database', 'x', '--report-root', 'y', *arguments])
    assert exc.value.code == 2


def test_decode_refuses_wrong_checksum_or_untyped_bundle():
    from intraday.replay_v2.historical_study import decode_inputs
    from intraday.replay_v2.historical_mixed import HistoricalConfig

    cfg = HistoricalConfig(start=START, end=START+WIDTH)
    for raw in ([], {}, {'bundle_checksum': '0'*64}):
        with pytest.raises(ValueError):
            decode_inputs(cfg, raw)
