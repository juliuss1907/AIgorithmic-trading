from datetime import timedelta
from decimal import Decimal as D

import pytest

from test_historical_mixed_research import START, WIDTH, fixture_inputs
from test_intraday_timeframe_data import native_bundle


def test_full_capital_presets_and_legacy_allocation_validation():
    from intraday.replay_v2.single_sleeve_research import SpotOnlyConfig, PerpOnlyConfig, WEIGHTS
    from intraday.replay_v2.historical_mixed import HistoricalConfig
    spot, perp = (cls(start=START, end=START+WIDTH) for cls in (SpotOnlyConfig, PerpOnlyConfig))
    assert spot.entry_cap == 1 and spot.perp_cap == spot.reserve == 0
    assert perp.perp_cap == 1 and perp.entry_cap == perp.reserve == 0
    assert spot.weights == perp.weights == perp.perp_weights == WEIGHTS
    assert perp.leverage == 3 and perp.perp_trailing_interval == '15m'
    with pytest.raises(ValueError):
        HistoricalConfig(start=START, end=START+WIDTH, entry_cap=1)


def test_perp_full_margin_is_three_times_notional_not_one_third_invested():
    from intraday.replay_v2.single_sleeve_research import PerpOnlyBook, PerpOnlyConfig
    cfg = PerpOnlyConfig(start=START, end=START+WIDTH)
    b = PerpOnlyBook(cfg)
    marks = {s:D(100) for s in cfg.weights}
    b.perp_marks = dict(marks)
    for s in sorted(cfg.perp_weights):
        assert b.enter_perp(s, START, marks, D(100), 1, D('.10'))
    notional = sum(abs(p.quantity)*p.entry_price for p in b.perps.values())
    assert 2900 < notional < 3000
    assert b.locked_margin+b.fees+b.slippage <= 1000
    assert b.free_cash >= 0
    before = b.perp_budget(marks)
    b.perp_marks = {s:D(150) for s in marks}
    assert b.perp_budget(marks) == before  # Unrealized profit is not new sizing.
    for s in sorted(cfg.perp_weights):
        b.close_perp(s, START+WIDTH, D(110), 'test')
    assert b.perp_budget(marks) == 1000+sum(t['net_pnl'] for t in b.trades)
    assert b.perp_budget(marks) > 1000 and b.realized_capital['spot'] == 0


def test_spot_only_reinvests_realized_profit_without_fixed_initial_cap():
    from intraday.replay_v2.single_sleeve_research import SpotOnlyConfig
    from intraday.replay_v2.historical_capital import HistoricalBook
    b = HistoricalBook(SpotOnlyConfig(start=START, end=START+2*WIDTH))
    marks = {s:D(100) for s in b.weights}
    b.enter_batch(START, marks, {s:D(1) for s in marks})
    assert b.cash >= 0 and b.spot_budget(marks) < 1000
    unrealized = {s:D(110) for s in marks}
    before = b.spot_budget(marks)
    assert b.spot_budget(unrealized) == before
    for s in marks:
        b.close(s, START+WIDTH, D(110), 'test')
    b.enter_batch(START+WIDTH, unrealized, {s:D(1) for s in marks})
    assert sum(p.quantity*p.entry_price for p in b.positions.values()) > 1000
    assert not any(p.quantity for p in b.perps.values())


def test_perp_only_three_coins_and_m15_trailing_reproduce(tmp_path):
    from intraday.replay_v2.single_sleeve_research import PerpOnlyConfig, simulate_single_sleeve
    from intraday.replay_v2.intraday_data import decode_inputs
    cfg = PerpOnlyConfig(start=START, end=START+timedelta(hours=8), trend_filter=False)
    spot, daily, perp, funding = fixture_inputs(cfg)
    native = decode_inputs(cfg, native_bundle(cfg, tmp_path), perp)
    for series in native.values():
        rows = list(series['trade1h'])
        rows[30] = rows[30].model_copy(update={'high':D(105), 'close':D(105)})
        rows[31] = rows[31].model_copy(update={'high':D(107), 'close':D(107)})
        rows[32] = rows[32].model_copy(update={'open':D(107), 'high':D(107), 'close':D(107)})
        series['trade1h'] = tuple(rows)
        rows = list(series['trade15m'])
        for i, op, close in [(3,100,107), (4,107,103), (5,107,107), (6,107,107), (7,107,107)]:
            rows[i] = rows[i].model_copy(update={'open':D(op), 'high':D(107), 'close':D(close)})
        series['trade15m'] = tuple(rows)
    r = simulate_single_sleeve(cfg, spot, daily, perp, funding, native)
    assert r == simulate_single_sleeve(cfg, spot, daily, perp, funding, native)
    assert all(t['market'] == 'perp' for t in r['trades'])
    assert set(t['symbol'] for t in r['trades']) == set(cfg.weights)
    assert all(t['exit_reason'] == 'perp_trade_trailing' for t in r['trades'])
    assert r['config']['market'] == 'perp-only'
