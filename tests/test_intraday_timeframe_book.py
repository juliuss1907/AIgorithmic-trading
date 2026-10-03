from datetime import timedelta
from decimal import Decimal

from test_historical_mixed_research import START, WIDTH


def test_m15_perp_risk_does_not_sample_parent_or_spot():
    from intraday.replay_v2.intraday_book import IntradayBook, IntradayConfig
    cfg = IntradayConfig(start=START, end=START+WIDTH, trend_filter=False)
    book = IntradayBook(cfg)
    marks = {s: Decimal(100) for s in cfg.weights}
    book.perp_marks = {s: Decimal(100) for s in cfg.perp_weights}
    book.enter_perp('BTCUSDT', START, marks, Decimal(100), 1, Decimal('.10'))
    parent = (book.peak, book.max_drawdown, book.last_equity, book.day_start, len(book.curve))
    book.perp_marks['BTCUSDT'] = Decimal(93)
    book.enforce_perp_risk(START+timedelta(minutes=15))
    assert book.daily.locked and book.daily.reason == 'perp_daily_loss'
    assert not book.halted
    assert parent == (book.peak, book.max_drawdown, book.last_equity, book.day_start, len(book.curve))
    assert book.perp_curve[-1]['perp_daily_locked']


def test_parent_has_priority():
    from intraday.replay_v2.intraday_book import IntradayBook, IntradayConfig
    cfg = IntradayConfig(start=START, end=START+timedelta(days=2), trend_filter=False)
    book = IntradayBook(cfg)
    marks = {s: Decimal(100) for s in cfg.weights}
    book.perp_marks = {s: Decimal(100) for s in cfg.perp_weights}
    book.enter_perp('BTCUSDT', START, marks, Decimal(100), 1, Decimal('.20'))
    book.perp_marks['BTCUSDT'] = Decimal(60)
    book.enforce_parent_risk(START, marks)
    assert book.halted and book.halt_reason == 'daily_loss_limit'
    assert not book.daily.locked


def test_perp_cannot_resume_same_tick_as_flatten_or_reset_trade_peak_overnight():
    from intraday.replay_v2.intraday_book import IntradayBook, IntradayConfig
    cfg = IntradayConfig(start=START, end=START+timedelta(days=2), trend_filter=False)
    book = IntradayBook(cfg)
    marks = {s: Decimal(100) for s in cfg.weights}
    book.perp_marks = {s: Decimal(100) for s in cfg.perp_weights}
    book.enter_perp('BTCUSDT', START, marks, Decimal(100), 1, Decimal('.20'))
    book.trade_trailing['BTCUSDT'].observe(Decimal('.06'))
    book.advance_day(START+timedelta(days=1))
    assert book.trade_trailing['BTCUSDT'].floor == Decimal('.03')
    book.perp_marks['BTCUSDT'] = Decimal(93)
    book.enforce_perp_risk(START+timedelta(days=1))
    at = START+timedelta(days=2)
    book.close_perp('BTCUSDT', at, Decimal(93), 'perp_daily_loss')
    book.maybe_resume_perp(at)
    assert book.daily.locked
    book.maybe_resume_perp(at+timedelta(minutes=15))
    assert not book.daily.locked


def test_intraday_config_does_not_change_legacy_defaults():
    from intraday.replay_v2.intraday_book import IntradayConfig
    from intraday.replay_v2.historical_mixed import HistoricalConfig
    cfg = IntradayConfig(start=START, end=START+WIDTH)
    assert cfg.perp_signal_interval == '1h' and cfg.perp_risk_interval == '15m'
    assert cfg.perp_trend_profile == '4h+8h-ema50'
    assert HistoricalConfig(start=START, end=START+WIDTH).perp_trailing_interval == '4h'
