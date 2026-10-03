from datetime import timedelta
from decimal import Decimal

import pytest

from intraday.replay_v2.historical_mixed import HistoricalConfig
from intraday.replay_v2.perp_daily import PerpDailyBook, PerpDailyState
from test_historical_mixed_research import START, WIDTH


def state(policy='target3'):
    return PerpDailyState(Decimal(300), START, policy)


def book(policy='target3'):
    cfg = HistoricalConfig(start=START, end=START+2*timedelta(days=1),
                           perp_daily_policy=policy, trend_filter=False, perp_stop='fixed-5pct')
    ledger = PerpDailyBook(cfg)
    marks = {s: Decimal(100) for s in cfg.weights}
    ledger.perp_marks = {s: Decimal(100) for s in cfg.perp_weights}
    return ledger, marks


@pytest.mark.parametrize('policy,equity,reason', [
    ('none', '291', 'perp_daily_loss'), ('target3', '309', 'perp_daily_profit'),
    ('target5', '315', 'perp_daily_profit'), ('target3', '308.99', None),
])
def test_exact_daily_thresholds(policy, equity, reason):
    daily = state(policy)
    daily.observe(START, Decimal(equity))
    assert daily.evaluate(START) == reason
    assert daily.locked == bool(reason)


def test_trailing_arms_at_three_percent_and_peak_never_decreases():
    daily = state('trailing')
    for amount in ('308.99', '309', '313.5', '312'):
        daily.observe(START, Decimal(amount))
        assert daily.evaluate(START) is None
    assert daily.armed and daily.peak_return == Decimal('.045')
    daily.observe(START, Decimal('310.5'))
    assert daily.evaluate(START) == 'perp_daily_trailing'
    daily.observe(START, Decimal(315))
    assert daily.evaluate(START) is None and daily.locked


def test_next_day_baseline_carries_equity_not_total_lifetime_pnl():
    daily = state('none')
    daily.observe(START, Decimal(306))
    daily.advance_day(START+timedelta(days=1))
    daily.observe(START+timedelta(days=1), Decimal(309))
    assert daily.day_start == 306
    assert daily.daily_return == Decimal(3)/306
    assert not daily.armed


@pytest.mark.parametrize('value', ['NaN', 'Infinity', '0', '-1'])
def test_invalid_capital_and_nonfinite_samples_are_rejected(value):
    with pytest.raises(ValueError):
        PerpDailyState(Decimal(value), START, 'none')
    if value in ('NaN', 'Infinity'):
        with pytest.raises(ValueError):
            state().observe(START, Decimal(value))


def test_perp_ledger_includes_entry_exit_costs_funding_and_not_spot():
    ledger, marks = book('none')
    ledger.enter_perp('BTCUSDT', START, marks, Decimal(100), 1, Decimal('.05'))
    assert ledger.perp_equity() == Decimal('299.85')
    ledger.settle_funding('BTCUSDT', START, Decimal('.001'), Decimal(100))
    ledger.perp_marks['BTCUSDT'] = Decimal(110)
    assert ledger.perp_equity() == Decimal('314.70')
    ledger.enter_batch(START, marks, {'SOLUSDT': Decimal(1)})
    marks['SOLUSDT'] = Decimal(200)
    assert ledger.perp_equity() == Decimal('314.70')
    ledger.close_perp('BTCUSDT', START+WIDTH, Decimal(110), 'test_exit')
    assert ledger.perp_equity() == Decimal('314.535')
    assert ledger.daily.cash == 300+ledger.trades[-1]['net_pnl']


def test_stop_only_closes_one_position_and_net_pnl_already_covers_losses():
    ledger, marks = book('none')
    ledger.enter_perp('BTCUSDT', START, marks, Decimal(100), 1, Decimal('.05'))
    ledger.enter_perp('ETHUSDT', START, marks, Decimal(100), 1, Decimal('.05'))
    ledger.perp_marks['ETHUSDT'] = Decimal(110)
    ledger.close_perp('BTCUSDT', START+WIDTH, Decimal(95), 'contract_stop_detected_at_close')
    ledger.enforce_risk(START+WIDTH, marks)
    assert ledger.perps['ETHUSDT'].quantity and not ledger.daily.locked
    assert ledger.daily.daily_return > 0


def test_perp_lock_does_not_lock_spot_and_resume_requires_next_day_and_flat():
    ledger, marks = book()
    ledger.enter_perp('BTCUSDT', START, marks, Decimal(100), 1, Decimal('.05'))
    ledger.perp_marks['BTCUSDT'] = Decimal(107)
    ledger.enforce_risk(START+WIDTH, marks)
    assert ledger.daily.locked and not ledger.halted
    assert not ledger.enter_perp('ETHUSDT', START+WIDTH, marks, Decimal(100), 1, Decimal('.05'))
    ledger.enter_batch(START+WIDTH, marks, {'SOLUSDT': Decimal(1)})
    assert ledger.positions['SOLUSDT'].quantity
    next_day = START+timedelta(days=1)
    ledger.advance_day(next_day)
    ledger.maybe_resume_perp(next_day)
    assert ledger.daily.locked  # Not flat yet.
    ledger.close_perp('BTCUSDT', next_day, Decimal(107), ledger.daily.reason)
    ledger.maybe_resume_perp(next_day)
    assert ledger.daily.locked  # No same-tick reopen after delayed flatten.
    ledger.maybe_resume_perp(next_day+WIDTH)
    assert not ledger.daily.locked


def test_daily_preset_has_eight_policies_and_one_spot_control():
    from intraday.replay_v2.historical_study import study_configs

    configs = study_configs(START, START+WIDTH, preset='perp-daily-policy')
    assert len(configs) == len({cfg.model_dump_json() for _, cfg in configs}) == 9
    assert {(c.perp_stop, c.perp_daily_policy) for _, c in configs if c.include_perp} == {
        (stop, policy) for stop in ('fixed-5pct', 'atr14-3x')
        for policy in ('none', 'target3', 'target5', 'trailing')}
    assert all(c.daily_loss == Decimal('.03') and c.perp_size == 'full' for _, c in configs)
    assert configs[-1][1].perp_daily_policy == 'disabled'


def test_cli_daily_preset_is_explicit_and_historical_only(monkeypatch):
    from intraday.replay_v2 import historical_study, mixed_portfolio_research as cli

    calls = []
    monkeypatch.setattr(historical_study, 'run_study', lambda *a, **kw: calls.append(kw) or
                        {'results': [None]*9, 'source_unchanged': True})
    cli.main(['--mode', 'historical-quant', '--preset', 'perp-daily-policy',
              '--database', 'x', '--report-root', 'y', '--perp-inputs', 'z'])
    assert calls[0]['preset'] == 'perp-daily-policy'
    with pytest.raises(SystemExit):
        cli.main(['--preset', 'perp-daily-policy', '--database', 'x', '--report-root', 'y'])


@pytest.mark.parametrize('policy', ['target3', 'target5'])
def test_close_trigger_flattens_perp_at_next_contract_open_not_mark(policy):
    from intraday.replay_v2.historical_mixed import simulate_historical
    from test_historical_mixed_research import fixture_inputs

    cfg = HistoricalConfig(start=START, end=START+2*WIDTH, trend_filter=False,
                           perp_stop='fixed-5pct', perp_daily_policy=policy)
    spot, daily, perp, funding = fixture_inputs(cfg)
    for data in perp.values():
        history = list(data['trade'])
        history[-2] = history[-2].model_copy(update={'close': Decimal(107), 'high': Decimal(108)})
        history[-1] = history[-1].model_copy(update={'open': Decimal(106), 'close': Decimal(106),
                                                   'high': Decimal(108), 'low': Decimal(105)})
        data['trade'] = tuple(history)
        mark = list(data['mark'])
        mark[0] = mark[0].model_copy(update={'close': Decimal(107), 'high': Decimal(108)})
        mark[1] = mark[1].model_copy(update={'open': Decimal(108), 'close': Decimal(108),
                                           'high': Decimal(109), 'low': Decimal(107)})
        data['mark'] = tuple(mark)
    report = simulate_historical(cfg, spot, daily, perp, funding)
    halts = [e for e in report['events'] if e['kind'] == 'perp_daily_halt']
    assert len(halts) == 1 and halts[0]['at'] == (START+WIDTH-timedelta(milliseconds=1)).isoformat()
    assert all(t['closed_at'] == (START+WIDTH).isoformat() and t['exit_price'] == '106'
               and t['exit_reason'] == 'perp_daily_profit' for t in report['trades'])
    summary = report['summary']['perp_daily']
    assert summary['profit_halts'] == 1 and summary['initial_equity'] == 300
    assert summary['net_pnl'] == pytest.approx(sum(float(t['net_pnl']) for t in report['trades']))
    assert summary['evidence'] == 'insufficient_perp_trades'


def test_parent_drawdown_has_priority_and_never_resets_with_daily_lock():
    ledger, marks = book('none')
    ledger.enter_perp('BTCUSDT', START, marks, Decimal(100), 1, Decimal('.05'))
    ledger.perp_marks['BTCUSDT'] = Decimal(93)
    ledger.peak = Decimal(1200)
    ledger.enforce_risk(START+WIDTH, marks)
    assert ledger.halt_reason == 'max_drawdown'
    assert not ledger.daily.locked
    ledger.close_perp('BTCUSDT', START+WIDTH, Decimal(93), 'max_drawdown')
    next_day = START+timedelta(days=1)
    ledger.advance_day(next_day)
    ledger.maybe_resume(next_day, marks)
    ledger.maybe_resume_perp(next_day)
    assert ledger.halted and ledger.halt_reason == 'max_drawdown'


def test_disabled_policy_retains_legacy_journal_shape_and_identity():
    from intraday.replay_v2.historical_mixed import simulate_historical
    from test_historical_mixed_research import fixture_inputs

    cfg = HistoricalConfig(start=START, end=START+WIDTH, trend_filter=False)
    report = simulate_historical(cfg, *fixture_inputs(cfg))
    assert 'perp_daily_policy' not in report['config']
    assert 'perp_daily' not in report['summary']
    assert all('perp_equity' not in row for row in report['equity_curve'])
    assert report['evaluator_version'] == 'historical-mixed-quant-v1.2'


def test_daily_study_publishes_nine_reproducible_journals(tmp_path):
    from intraday.backups import create_backup
    from intraday.store import IntradayStore
    from intraday.replay_v2.historical_study import run_study
    from test_historical_mixed_research import fixture_inputs

    source = create_backup(IntradayStore(tmp_path/'source.sqlite3').database, tmp_path/'evidence')
    cfg = HistoricalConfig(start=START, end=START+WIDTH, trend_filter=False)
    spot, daily, perp, funding = fixture_inputs(cfg)
    receipt = run_study(source['backup'], tmp_path/'reports', start=cfg.start, end=cfg.end,
        loader=lambda store, config: (spot, daily), perp_inputs=(perp, funding), trend_filter=False,
        preset='perp-daily-policy')
    assert len(receipt['results']) == len({r['result_id'] for r in receipt['results']}) == 9
    assert all(r['deterministic_rerun_verified'] for r in receipt['results'])
    assert all(r['paired_no_profit_cap_return_difference_pp'] is not None
               for r in receipt['results'] if r['include_perp'])
    assert receipt['source_unchanged']
    assert 'Perp sleeve' in (tmp_path/'reports'/'comparison.md').read_text()


def test_funding_can_trigger_daily_loss_without_parent_halt():
    ledger, marks = book('none')
    ledger.enter_perp('BTCUSDT', START, marks, Decimal(100), 1, Decimal('.05'))
    ledger.settle_funding('BTCUSDT', START+WIDTH, Decimal('.06'), Decimal(100))
    ledger.enforce_risk(START+WIDTH, marks)
    assert ledger.daily.reason == 'perp_daily_loss' and not ledger.halted
    assert ledger.daily.cash == Decimal('290.85')


def test_time_cannot_move_back_after_advance_without_observation():
    daily = state()
    daily.advance_day(START+timedelta(days=1))
    with pytest.raises(ValueError, match='backwards'):
        daily.observe(START+WIDTH, Decimal(300))


@pytest.mark.parametrize('field,value', [('perp_cap', 'NaN'), ('capital', 'Infinity'),
                                        ('perp_daily_policy', 'unknown')])
def test_daily_config_rejects_nonfinite_and_unknown_policies(field, value):
    with pytest.raises(ValueError):
        HistoricalConfig(start=START, end=START+WIDTH, **{field: value})


def test_missing_funding_daily_report_does_not_claim_full_return():
    from intraday.replay_v2.historical_mixed import simulate_historical
    from test_historical_mixed_research import fixture_inputs

    cfg = HistoricalConfig(start=START, end=START+WIDTH, trend_filter=False, perp_daily_policy='none')
    spot, daily, perp, _ = fixture_inputs(cfg)
    report = simulate_historical(cfg, spot, daily, perp, {})
    assert report['summary']['net_return_pct'] is None
    assert report['summary']['perp_daily']['net_pnl'] is None
    assert report['summary']['perp_daily']['pnl_after_known_costs'] is not None


def test_loss_flatten_blocks_same_open_reentry_but_spot_survives():
    from intraday.replay_v2.historical_mixed import open_exits, open_entries, PerpObservation
    from test_historical_mixed_research import bars

    ledger, marks = book('none')
    ledger.enter_batch(START, marks, {'SOLUSDT': Decimal(1)})
    ledger.enter_perp('BTCUSDT', START, marks, Decimal(100), 1, Decimal('.05'))
    ledger.enter_perp('ETHUSDT', START, marks, Decimal(100), 1, Decimal('.05'))
    ledger.perp_marks.update({s: Decimal(96) for s in ledger.perps})
    ledger.enforce_risk(START+WIDTH, marks)
    assert ledger.daily.reason == 'perp_daily_loss' and not ledger.halted
    perp_bars = {s: bars(START+WIDTH, 1, 96)[0] for s in ledger.perps}
    obs = {s: PerpObservation(1, False, False, Decimal(2)) for s in ledger.perps}
    closed = set()
    open_exits(ledger, START+WIDTH, {}, perp_bars, {}, obs, closed)
    open_entries(ledger, START+WIDTH, marks, perp_bars, {}, obs, {}, {}, set())
    assert all(not p.quantity for p in ledger.perps.values())
    assert ledger.positions['SOLUSDT'].quantity
    assert sum(e['reason'] == 'perp_daily_loss' and e['kind'] == 'entry_blocked'
               for e in ledger.events) == 2
    assert all(t['exit_reason'] == 'perp_daily_loss' for t in ledger.trades)
