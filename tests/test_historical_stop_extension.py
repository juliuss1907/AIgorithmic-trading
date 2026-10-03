from decimal import Decimal

import pytest

from test_historical_mixed_research import START, WIDTH, fixture_inputs
from intraday.replay_v2.historical_mixed import HistoricalConfig, simulate_historical, stop_fraction
from intraday.replay_v2.historical_study import study_configs


@pytest.mark.parametrize('stop,expected', [('fixed-5pct', '.05'), ('atr14-3x', '.06')])
def test_new_stop_distances(stop, expected):
    cfg = HistoricalConfig(start=START, end=START+WIDTH, perp_stop=stop)
    assert stop_fraction(cfg, Decimal(100), Decimal(2)) == Decimal(expected)


@pytest.mark.parametrize('side', [1, -1])
@pytest.mark.parametrize('stop', ['fixed-5pct', 'atr14-3x'])
@pytest.mark.parametrize('size,notional', [('full', '150'), ('two-thirds', '100')])
def test_extension_entries_size_margin_costs_and_stop_direction(side, stop, size, notional):
    cfg = HistoricalConfig(start=START, end=START+WIDTH, trend_filter=False,
                           perp_stop=stop, perp_size=size)
    report = simulate_historical(cfg, *fixture_inputs(cfg, side))
    entries = [e for e in report['events'] if e['kind'] == 'entry']
    assert len(entries) == 2
    expected_notionals = [Decimal(notional), Decimal('149.955') if size == 'full' else Decimal('99.99')]
    for entry, expected_notional in zip(entries, expected_notionals, strict=True):
        assert abs(Decimal(entry['quantity']))*Decimal(entry['price']) == expected_notional
        expected = Decimal(100)*(1-side*Decimal(entry['stop_fraction']))
        assert Decimal(entry['stop_price']) == expected
    assert report['summary']['exchange_fee_known'] == pytest.approx(float(sum(expected_notionals)*Decimal('.001')))
    assert report['summary']['max_isolated_margin_pct'] <= float(Decimal(notional)*2/3/1000*100)+.01
    assert all(Decimal(p['free_cash']) >= 100 for p in report['equity_curve'])


def test_extension_preset_is_eight_unique_configs_and_baseline_unchanged():
    baseline = study_configs(START, START+WIDTH)
    assert len(baseline) == 6
    assert {c.perp_stop for _, c in baseline if c.include_perp} == {'fixed-1pct', 'atr14-2x'}
    variants = study_configs(START, START+WIDTH, preset='stop-extension')
    assert len(variants) == 8
    assert len({c.model_dump_json() for _, c in variants}) == 8
    assert {(c.perp_stop, c.perp_size) for _, c in variants if c.include_perp} == {
        ('fixed-5pct', 'full'), ('atr14-3x', 'full'), ('atr14-3x', 'two-thirds')}
    with pytest.raises(ValueError):
        study_configs(START, START+WIDTH, preset='unknown')
    with pytest.raises(ValueError):
        HistoricalConfig(start=START, end=START+WIDTH, perp_size='unknown')


def test_cli_extension_preset_routes_only_to_historical(monkeypatch):
    from intraday.replay_v2 import historical_study, mixed_portfolio_research as cli

    calls = []
    monkeypatch.setattr(historical_study, 'run_study', lambda *a, **kw: calls.append(kw) or
                        {'results': [None]*8, 'source_unchanged': True})
    cli.main(['--mode', 'historical-quant', '--preset', 'stop-extension', '--database', 'x',
              '--report-root', 'y', '--perp-inputs', 'z'])
    assert calls[0]['preset'] == 'stop-extension'
    with pytest.raises(SystemExit):
        cli.main(['--preset', 'stop-extension', '--database', 'x', '--report-root', 'y'])


def test_extension_study_reproduces_eight_complete_journals(tmp_path):
    from intraday.backups import create_backup
    from intraday.store import IntradayStore
    from intraday.replay_v2.historical_study import run_study

    source = create_backup(IntradayStore(tmp_path/'source.sqlite3').database, tmp_path/'evidence')
    cfg = HistoricalConfig(start=START, end=START+WIDTH, trend_filter=False)
    spot, daily, perp, funding = fixture_inputs(cfg)
    receipt = run_study(source['backup'], tmp_path/'reports', start=cfg.start, end=cfg.end,
        loader=lambda store, config: (spot, daily), perp_inputs=(perp, funding), trend_filter=False,
        preset='stop-extension')
    assert len(receipt['results']) == len({r['result_id'] for r in receipt['results']}) == 8
    assert all(r['deterministic_rerun_verified'] for r in receipt['results'])
    assert receipt['preset'] == 'stop-extension' and receipt['source_unchanged']
    assert 'Perp size' in (tmp_path/'reports'/'comparison.md').read_text()


@pytest.mark.parametrize('stop', ['fixed-5pct', 'atr14-3x'])
@pytest.mark.parametrize('side', [1, -1])
def test_extended_stops_touch_and_gap_at_contract_prices(stop, side):
    from intraday.replay_v2.historical_mixed import close_touched_stops, open_exits, PerpObservation
    from intraday.replay_v2.mixed_book import MixedBook
    from test_historical_mixed_research import bars

    cfg = HistoricalConfig(start=START, end=START+WIDTH, trend_filter=False, perp_stop=stop)
    distance = stop_fraction(cfg, Decimal(100), Decimal(2))
    for gap in (False, True):
        book = MixedBook(cfg)
        marks = {s: Decimal(100) for s in cfg.weights}
        book.perp_marks = {s: Decimal(100) for s in cfg.perp_weights}
        book.enter_perp('BTCUSDT', START, marks, Decimal(100), side, distance)
        stop_price = book.perps['BTCUSDT'].stop
        extreme = stop_price-1 if side == 1 else stop_price+1
        bar = bars(START, 1)[0].model_copy(update={
            'open': extreme if gap else Decimal(100),
            'low': extreme if side == 1 else Decimal(99),
            'high': extreme if side == -1 else Decimal(101)})
        if gap:
            open_exits(book, START, {}, {'BTCUSDT': bar}, {},
                       {'BTCUSDT': PerpObservation(0, False, False, Decimal(2))}, set())
        else:
            close_touched_stops(book, START+WIDTH, {}, {'BTCUSDT': bar})
        assert book.trades[0]['exit_price'] == str(extreme if gap else stop_price)
        assert book.trades[0]['exit_reason'] == ('contract_stop_gap' if gap else 'contract_stop_detected_at_close')


def test_invalid_atr_stop_blocks_entry_instead_of_fabricating_distance():
    from intraday.replay_v2.historical_mixed import open_entries, PerpObservation
    from intraday.replay_v2.mixed_book import MixedBook
    from test_historical_mixed_research import bars

    cfg = HistoricalConfig(start=START, end=START+WIDTH, trend_filter=False, perp_stop='atr14-3x')
    for atr in (Decimal(0), Decimal(-1), Decimal(100)):
        book = MixedBook(cfg)
        marks = {s: Decimal(100) for s in cfg.weights}
        book.perp_marks = {s: Decimal(100) for s in cfg.perp_weights}
        open_entries(book, START, marks, {'BTCUSDT': bars(START, 1)[0]}, {},
                     {'BTCUSDT': PerpObservation(1, False, False, atr)}, {}, {}, set())
        assert book.flat
        assert any(e['reason'] == 'invalid_stop_distance' for e in book.events)


def test_two_thirds_scales_budget_before_shared_constraints():
    from intraday.replay_v2.historical_mixed import open_entries, PerpObservation
    from intraday.replay_v2.mixed_book import MixedBook
    from test_historical_mixed_research import bars

    cfg = HistoricalConfig(start=START, end=START+WIDTH, trend_filter=False,
                           perp_stop='atr14-3x', perp_size='two-thirds')
    book = MixedBook(cfg)
    marks = {s: Decimal(100) for s in cfg.weights}
    book.perp_marks = {s: Decimal(100) for s in cfg.perp_weights}
    # Existing BTC occupies 22%: only 8% remains in the shared Perp cap,
    # less than ETH's scaled 10% budget. Do not shrink that remaining 8% again.
    book.enter_perp('BTCUSDT', START, marks, Decimal(100), 1, Decimal('.06'))
    book.perps['BTCUSDT'].quantity = Decimal('2.2')
    book.perps['BTCUSDT'].margin = Decimal(220)/3
    target = book.perp_target('ETHUSDT', marks)[2]
    assert target < 100
    open_entries(book, START, marks, {'ETHUSDT': bars(START, 1)[0]}, {},
                 {'ETHUSDT': PerpObservation(1, False, False, Decimal(2))}, {}, {}, set())
    assert book.perps['ETHUSDT'].quantity*100 == target
