from datetime import timedelta
from decimal import Decimal as D

import pytest

from intraday.replay_v2.historical_mixed import HistoricalConfig, simulate_historical
from test_historical_mixed_research import START, WIDTH, fixture_inputs


def config(interval='4h'):
    return HistoricalConfig(start=START, end=START+WIDTH, trend_filter=False,
        perp_stop='atr14-3x', perp_daily_policy='none', perp_trade_exit='net-trailing-3pp',
        capital_growth='realized', drawdown_policy='observe-only', perp_trailing_interval=interval)


def finer_inputs(cfg, perp, side=1, *, spike=False, gap=False):
    from intraday.replay_v2.trailing_candle import TrailingCandle, WIDTHS
    width = WIDTHS[cfg.perp_trailing_interval]
    count = int((cfg.end-cfg.start)/width)
    path = [(100, 107), (107, 103), (104, 104)]+[(104, 100)]*(count-3)
    if gap:
        path = [(100, 107), (102, 104), (104, 104)]+[(104, 100)]*(count-3)
    if spike:
        path = [(100, 100)]*count
    def price(p):
        return D(p if side == 1 else 200-p)
    rows = tuple(TrailingCandle(interval=cfg.perp_trailing_interval,
        opened_at=START+i*width, available_at=START+(i+1)*width,
        open=price(o), close=price(c), high=max(price(o), price(c))+1+(10 if spike else 0),
        low=min(price(o), price(c))-1, volume=1) for i, (o, c) in enumerate(path))
    # The H4 envelope is the actual aggregate; boundary prices are unchanged.
    for data in perp.values():
        bar = data['trade'][-1].model_copy(update={
            'high': max(c.high for c in rows), 'low': min(c.low for c in rows)})
        data['trade'] = data['trade'][:-1]+(bar,)
    return {s: rows for s in cfg.perp_weights}


@pytest.mark.parametrize('interval', ['1h', '30m'])
@pytest.mark.parametrize('side', [1, -1])
def test_finer_close_latch_executes_next_native_open_not_h4(interval, side):
    cfg = config(interval)
    inputs = fixture_inputs(cfg, side)
    finer = finer_inputs(cfg, inputs[2], side)
    report = simulate_historical(cfg, *inputs, trailing_bars=finer)
    width = finer['BTCUSDT'][0].available_at-START
    assert all(t['exit_reason'] == 'perp_trade_trailing' for t in report['trades'])
    assert all(t['closed_at'] == (START+2*width).isoformat() for t in report['trades'])
    assert all(t['exit_price'] == str(104 if side == 1 else 96) for t in report['trades'])
    assert all(t['trailing_triggered_at'] == (START+2*width-timedelta(milliseconds=1)).isoformat()
               for t in report['trades'])
    assert all(e['at'] == START.isoformat() for e in report['events'] if e['kind'] == 'entry')
    # No regular fine observations: curve only H4/funding and actual fill/cost checks.
    fine_close = START+width-timedelta(milliseconds=1)
    assert not [p for p in report['equity_curve'] if p['at'] == fine_close.isoformat()]
    assert 'sampling_interval' in report['methodology']['perp_trade_trailing']
    h4 = simulate_historical(config(), *inputs)
    assert all(t['exit_reason'] == 'window_end' for t in h4['trades'])
    assert 'perp_trailing_interval' not in h4['config']
    assert report == simulate_historical(cfg, *inputs, trailing_bars=finer)


@pytest.mark.parametrize('interval', ['1h', '30m'])
def test_open_gap_uses_old_floor_without_waiting_for_close(interval):
    cfg = config(interval)
    inputs = fixture_inputs(cfg)
    finer = finer_inputs(cfg, inputs[2], gap=True)
    report = simulate_historical(cfg, *inputs, trailing_bars=finer)
    assert all(t['exit_price'] == '102' for t in report['trades'])
    assert all(t['closed_at'] == finer['BTCUSDT'][1].opened_at.isoformat() for t in report['trades'])


@pytest.mark.parametrize('interval', ['1h', '30m'])
def test_finer_intrabar_high_does_not_arm_trailing(interval):
    cfg = config(interval)
    inputs = fixture_inputs(cfg)
    report = simulate_historical(cfg, *inputs, trailing_bars=finer_inputs(cfg, inputs[2], spike=True))
    assert report['summary']['perp_trade_trailing']['armed_count'] == 0


def test_nondefault_cadence_requires_trailing_and_complete_evidence():
    with pytest.raises(ValueError):
        HistoricalConfig(start=START, end=START+WIDTH, perp_trailing_interval='1h')
    cfg = config('1h')
    inputs = fixture_inputs(cfg)
    with pytest.raises(ValueError, match='trailing'):
        simulate_historical(cfg, *inputs)
    finer = finer_inputs(cfg, inputs[2])
    finer['BTCUSDT'] = finer['BTCUSDT'][:-1]
    with pytest.raises(ValueError):
        simulate_historical(cfg, *inputs, trailing_bars=finer)


@pytest.mark.parametrize('interval', ['1h', '30m'])
def test_atr_and_daily_risk_do_not_run_at_finer_ticks(interval):
    from intraday.replay_v2.trailing_candle import WIDTHS
    cfg = config(interval)
    inputs = fixture_inputs(cfg)
    finer = finer_inputs(cfg, inputs[2], spike=True)
    width = WIDTHS[interval]
    for s, rows in finer.items():
        changed = rows[0].model_copy(update={'low': D(85)})
        finer[s] = (changed,)+rows[1:]
        bar = inputs[2][s]['trade'][-1].model_copy(update={'low': D(85)})
        inputs[2][s]['trade'] = inputs[2][s]['trade'][:-1]+(bar,)
    report = simulate_historical(cfg, *inputs, trailing_bars=finer)
    assert all(t['exit_reason'] == 'contract_stop_detected_at_close' for t in report['trades'])
    assert all(t['closed_at'] == (cfg.end-timedelta(milliseconds=1)).isoformat() for t in report['trades'])
    assert not [p for p in report['equity_curve'] if p['at'] == (START+width).isoformat()]


@pytest.mark.parametrize('priority', ['contract_stop_gap', 'perp_daily_loss', 'max_drawdown'])
def test_h4_boundary_keeps_existing_risk_exit_priority(priority, monkeypatch):
    from intraday.replay_v2.trade_trailing import TradeTrailingBook
    cfg = config('1h').model_copy(update={'end': START+2*WIDTH})
    inputs = fixture_inputs(cfg)
    from intraday.replay_v2.trailing_candle import TrailingCandle
    path = [(100, 100), (100, 100), (100, 100), (100, 107), (102, 100)]+[(100, 100)]*3
    rows = tuple(TrailingCandle(interval='1h', opened_at=START+timedelta(hours=i),
        available_at=START+timedelta(hours=i+1), open=o, close=c,
        high=max(o,c)+1, low=min(o,c)-1, volume=1) for i,(o,c) in enumerate(path))
    for data in inputs[2].values():
        first, second = data['trade'][-2:]
        data['trade'] = data['trade'][:-2]+(first.model_copy(update={'close': D(107), 'high': D(108)}),
            second.model_copy(update={'open': D(102), 'high': D(103)}))
    # Set the competing condition before the common H4 open is evaluated.
    advance = TradeTrailingBook.advance_day
    def advance_hook(book, at):
        advance(book, at)
        if at == START+WIDTH:
            if priority == 'contract_stop_gap':
                for p in book.perps.values():
                    if p.quantity:
                        p.stop = D(103)
            elif priority == 'perp_daily_loss':
                book.daily.locked, book.daily.reason = True, priority
                book.daily.until = START+timedelta(days=1)
            else:
                book.halted, book.halt_reason = True, priority
    monkeypatch.setattr(TradeTrailingBook, 'advance_day', advance_hook)
    report = simulate_historical(cfg, *inputs, trailing_bars={s: rows for s in cfg.perp_weights})
    assert all(t['exit_reason'] == priority for t in report['trades'])
