from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

from intraday import setup2, trade_alerts
from intraday.execution.contracts import AccountRef, ExecutionFill, OrderIntent, OrderUpdate
from intraday.execution.journal import ExecutionJournal

ACCOUNT = AccountRef(venue="binance", environment="demo", account_id="test", market="spot")
T0 = datetime(2026, 10, 14, 8, tzinfo=timezone.utc)


class Bot:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def send(self, message):
        if self.fail:
            raise RuntimeError("telegram down")
        self.sent.append(message)


def order(journal, intent_id, status, *, at, order_type="MARKET", side="BUY", source_id="decision-1",
          stop=None, quantity=D("2"), price=None):
    intent = OrderIntent(intent_id=intent_id, account=ACCOUNT, symbol="ETHUSDT", market="spot", side=side,
                         quantity=quantity, order_type=order_type, stop_price=stop, source_id=source_id, created_at=at)
    journal.prepare(intent)
    fills = () if price is None else (ExecutionFill(fill_id=intent_id+"-f", quantity=quantity, price=price,
                                                    commission=D("0.1"), commission_asset="USDT", filled_at=at),)
    journal.record(intent, OrderUpdate(intent_id=intent_id, status=status, received_at=at,
                                       executed_quantity=quantity if price else D(0), average_price=price, fills=fills))


def test_initial_stop_and_exit_level_follow_the_capped_trail_and_the_10_bar_channel():
    assert setup2.initial_stop(1, D(100), D(1), cap=D(".05")) == D(97)
    assert setup2.initial_stop(1, D(100), D(3), cap=D(".05")) == D("95.00")
    assert setup2.initial_stop(-1, D(100), D(1), cap=D(".05")) == D(103)
    bars = [setup2.Bar(T0+i*setup2.H4, T0+(i+1)*setup2.H4, D(10), D(11+i), D(9-i/10), D(10), D(1)) for i in range(12)]
    assert setup2.exit_level(bars, 1) == min(b.low for b in bars[-10:])
    assert setup2.exit_level(bars, -1) == max(b.high for b in bars[-10:])
    assert setup2.exit_level(bars[:9], 1) is None


def test_demo_alerts_send_entry_with_stop_then_trail_moves_and_exit_once(tmp_path):
    journal = ExecutionJournal(tmp_path / "execution.sqlite")
    order(journal, "old-entry", "FILLED", at=T0-timedelta(days=1), price=D(2400))
    bot = Bot()
    levels = lambda symbol, market, now: D(2380)
    assert trade_alerts.notify_demo(journal, bot, exit_level_for=levels, now=T0) is None   # baseline only
    assert bot.sent == []
    order(journal, "entry", "FILLED", at=T0, price=D(2500))
    order(journal, "stop-1", "NEW", at=T0, order_type="STOP_LOSS", side="SELL", source_id=None, stop=D(2425))
    assert trade_alerts.notify_demo(journal, bot, exit_level_for=levels, now=T0) == {"delivered": 1, "failed": 0}
    assert bot.sent == ["[DEMO] VÀO ETHUSDT Spot LONG\nKhớp: 2 @ 2500 (5000 USDT)\nStop: 2425 (-3.00%)\n"
                        "Thoát khi nến 4h đóng dưới: 2380 (kênh 10 nến)"]
    order(journal, "stop-2", "NEW", at=T0+setup2.H4, order_type="STOP_LOSS", side="SELL", source_id=None, stop=D(2450))
    order(journal, "close", "FILLED", at=T0+2*setup2.H4, side="SELL", source_id="close:donchian_exit", price=D(2600))
    trade_alerts.notify_demo(journal, bot, exit_level_for=levels, now=T0+2*setup2.H4)
    assert bot.sent[1] == "[DEMO] STOP ETHUSDT Spot: 2450 (-2.00% so với giá vào 2500)"
    assert bot.sent[2] == ("[DEMO] THOÁT ETHUSDT Spot LONG: phá kênh thoát 10 nến\nKhớp: 2 @ 2600 | giá vào 2500\n"
                           "PnL giá: +200.00 USDT (+4.00%)")
    assert trade_alerts.notify_demo(journal, bot, exit_level_for=levels, now=T0+3*setup2.H4) is None
    assert len(bot.sent) == 3


def test_demo_alert_failures_retry_a_bounded_number_of_times(tmp_path):
    journal = ExecutionJournal(tmp_path / "execution.sqlite")
    levels = lambda symbol, market, now: None
    trade_alerts.notify_demo(journal, Bot(), exit_level_for=levels, now=T0)
    order(journal, "entry", "FILLED", at=T0, price=D(2500))
    down = Bot(fail=True)
    results = [trade_alerts.notify_demo(journal, down, exit_level_for=levels, now=T0) for _ in range(6)]
    assert results[:5] == [{"delivered": 0, "failed": 1}]*5 and results[5] is None
    assert journal.notifications()[(ACCOUNT.key, "entry:fill")] == ("failed", 5)


def test_telegram_settings_come_from_the_worker_environment():
    assert trade_alerts.telegram_from_environment({"TELEGRAM_ENABLED": "false"}) is None
    assert trade_alerts.telegram_from_environment({"TELEGRAM_ENABLED": "true", "TELEGRAM_BOT_TOKEN": "t",
                                                   "TELEGRAM_CHAT_ID": "1"}) is not None
    try:
        trade_alerts.telegram_from_environment({"TELEGRAM_ENABLED": "true"})
    except ValueError as error:
        assert "TELEGRAM_BOT_TOKEN" in str(error)
    else:
        raise AssertionError("enabled Telegram without a bot must fail")
