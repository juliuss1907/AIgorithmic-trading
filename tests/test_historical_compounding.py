from decimal import Decimal

import pytest

from intraday.replay_v2.historical_mixed import HistoricalConfig, simulate_historical
from intraday.replay_v2.historical_study import study_configs
from intraday.replay_v2.perp_daily import PerpDailyBook
from test_historical_mixed_research import START, WIDTH, fixture_inputs


def growth_book(mode='equity'):
    cfg = HistoricalConfig(start=START, end=START+WIDTH, trend_filter=False,
                           capital_growth=mode, perp_daily_policy='target5', perp_stop='atr14-3x')
    book = PerpDailyBook(cfg)
    marks = {s: Decimal(100) for s in cfg.weights}
    book.perp_marks = {s: Decimal(100) for s in cfg.perp_weights}
    return book, marks


@pytest.mark.parametrize('cash,mode,base', [(1100, 'equity', 1100), (1100, 'capped', 1000),
                                         (900, 'equity', 900), (900, 'capped', 900)])
def test_both_markets_use_same_growth_base(cash, mode, base):
    book, marks = growth_book(mode)
    book.cash = Decimal(cash)
    assert book.perp_target('BTCUSDT', marks) == (Decimal(base), Decimal(base)*Decimal('.15'),
                                               Decimal(base)*Decimal('.15'))
    book.enter_batch(START, marks, {'BTCUSDT': Decimal(1)})
    entry = book.events[-1]
    assert Decimal(entry['base_equity']) == base
    assert Decimal(entry['notional']) == Decimal(base)*Decimal('.24')


def test_realized_gains_are_reinvested_and_daily_baseline_stays_perp_only():
    book, marks = growth_book()
    book.enter_perp('BTCUSDT', START, marks, Decimal(100), 1, Decimal('.05'))
    book.close_perp('BTCUSDT', START, Decimal(110), 'test_exit')
    assert book.cash == Decimal('1014.685')
    base, requested, target = book.perp_target('ETHUSDT', marks)
    assert base == book.cash and target == requested == base*Decimal('.15')
    book.enter_batch(START, marks, {'SOLUSDT': Decimal(1)})
    marks['SOLUSDT'] = Decimal(110)
    book.observe(START, marks)
    book.advance_day(START+WIDTH*6)
    assert book.daily.day_start == Decimal('314.685')
    assert book.daily.day_start != book.equity(marks)*Decimal('.30')


def test_unrealized_equity_counts_without_resizing_existing_positions():
    book, marks = growth_book()
    book.enter_batch(START, marks, {'SOLUSDT': Decimal(1)})
    quantity = book.positions['SOLUSDT'].quantity
    marks['SOLUSDT'] = Decimal(110)
    assert book.allocation_base(marks) == book.equity(marks) > book.config.capital
    book.enter_perp('BTCUSDT', START, marks, Decimal(100), 1, Decimal('.05'))
    assert book.positions['SOLUSDT'].quantity == quantity
    assert Decimal(book.events[-1]['base_equity']) > 1000


def test_all_reserve_and_shared_exposure_constraints_grow_together():
    book, marks = growth_book()
    book.cash = Decimal(2000)
    book.enter_batch(START, marks, {s: Decimal(1) for s in book.weights})
    assert book.exposures(marks)[0] == 1200
    for s in book.perps:
        book.enter_perp(s, START, marks, Decimal(100), 1, Decimal('.05'))
    equity = book.equity(marks)
    spot, perp = book.exposures(marks)
    assert perp > 590 and spot+perp > 1790  # Old initial-capital ceilings no longer bind.
    assert book.free_cash >= equity*book.config.reserve
    assert book.locked_margin*3 <= book.allocation_base(marks)*book.config.perp_cap


def test_two_thirds_growth_scales_requested_budget_not_initial_capital():
    from intraday.replay_v2.historical_mixed import open_entries, PerpObservation
    from test_historical_mixed_research import bars

    book, marks = growth_book()
    book.config = book.config.model_copy(update={'perp_size': 'two-thirds'})
    book.cash = Decimal(2000)
    open_entries(book, START, marks, {'BTCUSDT': bars(START, 1)[0]}, {},
                 {'BTCUSDT': PerpObservation(1, False, False, Decimal(2))}, {}, {}, set())
    assert book.perps['BTCUSDT'].quantity*100 == 200


def test_growth_is_opt_in_historical_only_and_preserves_old_report_identity():
    from intraday.replay_v2.mixed_book import MixedConfig

    cfg = HistoricalConfig(start=START, end=START+WIDTH, trend_filter=False, perp_daily_policy='target5')
    old = simulate_historical(cfg, *fixture_inputs(cfg))
    explicit = simulate_historical(cfg.model_copy(update={'capital_growth': 'capped'}), *fixture_inputs(cfg))
    assert old == explicit
    assert 'capital_growth' not in old['config']
    assert 'capital_growth' not in MixedConfig.model_fields
    new = simulate_historical(cfg.model_copy(update={'capital_growth': 'equity'}), *fixture_inputs(cfg))
    assert new['config']['capital_growth'] == 'equity'
    assert new['evaluator_version'] == 'historical-equity-growth-v1.0'
    assert old['result_id'] != new['result_id']
    assert old['inputs']['dataset_checksum'] == new['inputs']['dataset_checksum']
    with pytest.raises(ValueError):
        HistoricalConfig(start=START, end=START+WIDTH, capital_growth='unknown')


def test_compounding_preset_has_four_paired_configs():
    variants = study_configs(START, START+WIDTH, preset='perp-daily-compounding')
    assert len(variants) == 4
    assert {(c.include_perp, c.capital_growth) for _, c in variants} == {
        (True, 'capped'), (True, 'equity'), (False, 'capped'), (False, 'equity')}
    assert all(c.perp_stop == 'atr14-3x' and c.perp_daily_policy == 'target5'
               for _, c in variants if c.include_perp)


def test_cli_compounding_is_historical_only(monkeypatch):
    from intraday.replay_v2 import historical_study, mixed_portfolio_research as cli

    calls = []
    monkeypatch.setattr(historical_study, 'run_study', lambda *a, **kw: calls.append(kw) or
                        {'results': [None]*4, 'source_unchanged': True})
    cli.main(['--mode', 'historical-quant', '--preset', 'perp-daily-compounding', '--database', 'x',
              '--report-root', 'y', '--perp-inputs', 'z'])
    assert calls[0]['preset'] == 'perp-daily-compounding'
    with pytest.raises(SystemExit):
        cli.main(['--preset', 'perp-daily-compounding', '--database', 'x', '--report-root', 'y'])


def test_compounding_study_publishes_four_reproduced_paired_journals(tmp_path):
    from intraday.backups import create_backup
    from intraday.store import IntradayStore
    from intraday.replay_v2.historical_study import run_study

    source = create_backup(IntradayStore(tmp_path/'source.sqlite3').database, tmp_path/'evidence')
    cfg = HistoricalConfig(start=START, end=START+WIDTH, trend_filter=False)
    spot, daily, perp, funding = fixture_inputs(cfg)
    receipt = run_study(source['backup'], tmp_path/'reports', start=cfg.start, end=cfg.end,
        loader=lambda store, config: (spot, daily), perp_inputs=(perp, funding), trend_filter=False,
        preset='perp-daily-compounding')
    assert len(receipt['results']) == len({r['result_id'] for r in receipt['results']}) == 4
    assert receipt['source_unchanged'] and all(r['deterministic_rerun_verified'] for r in receipt['results'])
    for row in receipt['results']:
        control = next(r for r in receipt['results'] if not r['include_perp']
                       and r['capital_growth'] == row['capital_growth'])
        reference = next(r for r in receipt['results'] if r['include_perp'] == row['include_perp']
                         and r['capital_growth'] == 'capped')
        net = row['summary']['net_return_pct']
        assert row['paired_capped_return_difference_pp'] == net-reference['summary']['net_return_pct']
        assert row['paired_spot_control_return_difference_pp'] == (
            net-control['summary']['net_return_pct'] if row['include_perp'] else None)
    assert 'current mark-to-market equity' in (tmp_path/'reports'/'comparison.md').read_text()
