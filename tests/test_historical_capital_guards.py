from decimal import Decimal
from datetime import timedelta

import pytest

from intraday.replay_v2.historical_capital import HistoricalBook
from intraday.replay_v2.historical_mixed import HistoricalConfig, simulate_historical
from test_historical_mixed_research import START, WIDTH, fixture_inputs


def ledger(policy):
    cfg = HistoricalConfig(start=START, end=START+2*timedelta(days=1), trend_filter=False,
                           drawdown_policy=policy)
    book = HistoricalBook(cfg)
    marks = {s: Decimal(100) for s in cfg.weights}
    book.perp_marks = {s: Decimal(100) for s in cfg.perp_weights}
    return book, marks


@pytest.mark.parametrize('amount,reason', [('900.01', None), ('900', 'initial_capital_loss'),
                                         ('899', 'initial_capital_loss')])
def test_initial_capital_floor_exact_boundary(amount, reason):
    book, marks = ledger('initial-capital')
    book.cash = book.day_start = Decimal(amount)  # Isolate the lifetime floor from daily loss.
    book.enforce_risk(START, marks)
    assert book.halt_reason == reason


@pytest.mark.parametrize('policy,reason', [('terminal', 'max_drawdown'),
                                        ('observe-only', None), ('initial-capital', None)])
def test_initial_floor_does_not_trail_profit_peak(policy, reason):
    book, marks = ledger(policy)
    book.cash = Decimal(1200)
    book.observe(START, marks)
    book.cash = book.day_start = Decimal(950)
    book.enforce_risk(START+WIDTH, marks)
    assert book.halt_reason == reason
    assert book.peak == 1200 and book.max_drawdown == 1-Decimal(950)/1200


@pytest.mark.parametrize('policy', ['observe-only', 'initial-capital'])
def test_daily_pause_resumes_with_peak_drawdown_above_limit(policy):
    book, marks = ledger(policy)
    book.cash = Decimal(1200)
    book.observe(START, marks)
    book.cash = book.day_start = Decimal(950)
    book.enforce_risk(START+WIDTH, marks)
    book.cash = Decimal(920)
    book.enforce_risk(START+2*WIDTH, marks)
    assert book.halt_reason == 'daily_loss_limit'
    book.advance_day(START+timedelta(days=1))
    book.maybe_resume(START+timedelta(days=1), marks)
    assert not book.halted and book.day_start == 920
    book.enter_perp('BTCUSDT', START+timedelta(days=1), marks, Decimal(100), 1, Decimal('.05'))
    assert book.perps['BTCUSDT'].quantity


def test_initial_capital_halt_latches_even_after_price_recovery_and_next_day():
    book, marks = ledger('initial-capital')
    book.cash = book.day_start = Decimal(900)
    book.enforce_risk(START, marks)
    book.cash = Decimal(1100)
    book.enforce_risk(START+WIDTH, marks)
    book.advance_day(START+timedelta(days=1))
    book.maybe_resume(START+timedelta(days=1), marks)
    assert book.halted and book.halt_reason == 'initial_capital_loss'
    assert sum(e['kind']=='halt' for e in book.events) == 1
    assert not book.enter_perp('BTCUSDT', START+timedelta(days=1), marks, Decimal(100), 1, Decimal('.05'))


def test_daily_pause_upgrades_to_initial_floor_not_peak_based_halt():
    book, marks = ledger('initial-capital')
    book.cash = Decimal(960)
    book.enforce_risk(START, marks)
    assert book.halt_reason == 'daily_loss_limit'
    book.cash = Decimal(900)
    book.maybe_resume(START+timedelta(days=1), marks)
    assert book.halt_reason == 'initial_capital_loss'


def test_initial_floor_counts_unrealized_pnl_and_flattens_both_markets_at_contract_open():
    from intraday.replay_v2.historical_mixed import open_exits, PerpObservation
    from test_historical_mixed_research import bars

    book, marks = ledger('initial-capital')
    book.enter_batch(START, marks, {s: Decimal(1) for s in book.weights})
    for s in book.perps:
        book.enter_perp(s, START, marks, Decimal(100), 1, Decimal('.05'))
    cash_before_marking = book.cash
    marks = {s: Decimal(85) for s in book.weights}
    book.perp_marks = {s: Decimal(96) for s in book.perps}
    book.enforce_risk(START+WIDTH, marks)
    assert book.cash == cash_before_marking  # No realized sell needed to trigger.
    assert book.halt_reason == 'initial_capital_loss' and not book.flat
    spot_bars = {s: bars(START+WIDTH, 1, 85)[0] for s in book.weights}
    perp_bars = {s: bars(START+WIDTH, 1, 97)[0] for s in book.perps}
    # Parent floor reason has priority, so unused signal observations are not needed.
    open_exits(book, START+WIDTH, spot_bars, perp_bars, {},
               {s: PerpObservation(0, False, False, Decimal(2)) for s in book.perps}, set())
    assert book.flat and all(t['exit_reason']=='initial_capital_loss' for t in book.trades)
    assert {t['exit_price'] for t in book.trades if t['market']=='perp'} == {'97'}  # Not mark 96.
    assert {t['market'] for t in book.trades} == {'spot', 'perp'}


@pytest.mark.parametrize('policy', ['observe-only', 'initial-capital'])
@pytest.mark.parametrize('mark,reason', [('120', 'perp_daily_profit'), ('93', 'perp_daily_loss')])
def test_perp_daily_locks_unchanged_under_new_capital_policies(policy, mark, reason):
    from intraday.replay_v2.perp_daily import PerpDailyBook

    base, marks = ledger(policy)
    book = PerpDailyBook(base.config.model_copy(update={'perp_daily_policy': 'target5'}))
    book.perp_marks = dict(base.perp_marks)
    book.enter_perp('BTCUSDT', START, marks, Decimal(100), 1, Decimal('.06'))
    book.perp_marks['BTCUSDT'] = Decimal(mark)
    book.enforce_risk(START, marks)
    assert not book.halted and book.daily.locked and book.daily.reason == reason


@pytest.mark.parametrize('policy', ['observe-only', 'initial-capital'])
def test_collateral_guard_still_blocks_fabricated_continuation(policy):
    book, marks = ledger(policy)
    book.enter_perp('BTCUSDT', START, marks, Decimal(100), 1, Decimal('.05'))
    book.perp_marks['BTCUSDT'] = Decimal(50)
    book.check_isolated_collateral(START)
    book.enforce_risk(START, marks)
    assert book.halted and book.halt_reason == 'unsupported_liquidation'
    assert 'unsupported_liquidation' in book.limitations


def test_new_policy_reports_and_default_compatibility():
    cfg = HistoricalConfig(start=START, end=START+WIDTH, trend_filter=False)
    old = simulate_historical(cfg, *fixture_inputs(cfg))
    explicit = simulate_historical(cfg.model_copy(update={'drawdown_policy': 'terminal'}), *fixture_inputs(cfg))
    assert old == explicit and 'drawdown_policy' not in old['config']
    assert 'capital_guard' not in old['summary']
    for policy in ('observe-only', 'initial-capital'):
        report = simulate_historical(cfg.model_copy(update={'drawdown_policy': policy}), *fixture_inputs(cfg))
        guard = report['summary']['capital_guard']
        assert report['evaluator_version'] == 'historical-capital-guard-v1.1'
        assert report['config']['drawdown_policy'] == guard['policy'] == policy
        assert guard['initial_capital_floor'] == (900 if policy=='initial-capital' else None)
        assert guard['minimum_equity_known'] <= 1000
        assert guard['max_initial_capital_loss_pct'] >= 0
        assert report['inputs']['dataset_checksum'] == old['inputs']['dataset_checksum']
        assert report['result_id'] != old['result_id']
    with pytest.raises(ValueError):
        HistoricalConfig(start=START, end=START+WIDTH, drawdown_policy='unknown')


@pytest.mark.parametrize('policy', ['observe-only', 'initial-capital'])
def test_cli_guard_policy_is_historical_only(monkeypatch, policy):
    from intraday.replay_v2 import historical_study, mixed_portfolio_research as cli

    calls = []
    monkeypatch.setattr(historical_study, 'run_study', lambda *a, **kw: calls.append(kw) or
                        {'results': [None]*4, 'source_unchanged': True})
    cli.main(['--mode', 'historical-quant', '--preset', 'perp-daily-compounding', '--database', 'x',
              '--report-root', 'y', '--perp-inputs', 'z', '--drawdown-policy', policy])
    assert calls[0]['drawdown_policy'] == policy
    with pytest.raises(SystemExit):
        cli.main(['--database', 'x', '--report-root', 'y', '--drawdown-policy', policy])


@pytest.mark.parametrize('policy', ['observe-only', 'initial-capital'])
def test_guard_study_reproduces_four_journals_with_explicit_policy(tmp_path, policy):
    from intraday.backups import create_backup
    from intraday.store import IntradayStore
    from intraday.replay_v2.historical_study import run_study

    source = create_backup(IntradayStore(tmp_path/'source.sqlite3').database, tmp_path/'evidence')
    cfg = HistoricalConfig(start=START, end=START+WIDTH, trend_filter=False)
    spot, daily, perp, funding = fixture_inputs(cfg)
    receipt = run_study(source['backup'], tmp_path/'reports', start=cfg.start, end=cfg.end,
        loader=lambda store, config: (spot, daily), perp_inputs=(perp, funding), trend_filter=False,
        preset='perp-daily-compounding', drawdown_policy=policy)
    assert receipt['drawdown_policy'] == policy and receipt['source_unchanged']
    assert len(receipt['results']) == len({r['result_id'] for r in receipt['results']}) == 4
    assert all(r['deterministic_rerun_verified'] and r['drawdown_policy']==policy for r in receipt['results'])
    assert policy in (tmp_path/'reports'/'comparison.md').read_text()
