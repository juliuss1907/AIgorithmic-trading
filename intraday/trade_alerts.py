"""Telegram alerts for Setup-2 trades: soak signals (no order is placed) and Binance Demo fills.

Setup-2 has no take-profit: a position exits on its capped ATR trailing stop, on a close beyond the
10-bar Donchian exit channel, on a rotation or on a risk limit. Alerts therefore show the stop and the
exit channel level in place of a target.
"""

from __future__ import annotations

from decimal import Decimal

from intraday import setup2
from intraday.scoped_gate import jev_advice

MARKET_LABEL = {"spot": "Spot", "perp": "Perp"}
# Marker row of the Demo alert journal: present once the orders that existed before alerts were recorded.
BASELINE = ("*", "baseline")
EXIT_REASONS = {
    "donchian_exit": "phá kênh thoát 10 nến",
    "stop_loss": "chạm stop",
    "rotation_exit": "xoay vòng sang tín hiệu mạnh hơn",
    "loss_limit": "chạm giới hạn lỗ ngày hoặc drawdown",
    "signal_exit": "tín hiệu thoát",
    "protection_failure": "không đặt được stop bảo vệ",
    "operator": "đóng thủ công",
}


def _num(value) -> str:
    return format(float(value), ".6g")


def _pct(value) -> str:
    return format(float(value) * 100, "+.2f") + "%"


def _side_label(side: int) -> str:
    return "LONG" if side > 0 else "SHORT"


def _exit_line(side: int, level) -> str:
    if level is None:
        return "Kênh thoát 10 nến: chưa đủ dữ liệu"
    return f"Thoát khi nến 4h đóng {'dưới' if side > 0 else 'trên'}: {_num(level)} (kênh 10 nến)"


def soak_signal_message(*, symbol, market, observation, profile, exit_level, direction, confidence, allowed) -> str:
    """A rule entry on a closed bar and Jev's answer (``direction`` None when Jev failed). Soak places no
    order and does not track exits; the 3-position limit, the PnL gate and rotation apply only in the Demo
    runtime."""
    side = observation.side
    entry = Decimal(str(observation.close))
    share = Decimal(str(observation.size_multiplier)) / setup2.SLOT_DIVISOR
    stop = setup2.initial_stop(side, entry, observation.atr, cap=setup2.stop_cap(profile))
    verdict = "tín hiệu luật (soak, không đặt lệnh)"
    return "\n".join([
        f"[SOAK] {symbol} {MARKET_LABEL[market]} {_side_label(side)}: {verdict}",
        f"Nến 4h đóng: {observation.bar_close:%Y-%m-%d %H:%M} UTC",
        f"Giá vào tham chiếu: {_num(entry)} (giá đóng nến)",
        f"Khối lượng: {share * 100:.1f}% túi {MARKET_LABEL[market]} "
        f"(1/{setup2.SLOT_DIVISOR} túi × {float(observation.size_multiplier):.2f}, ATR {float(observation.atr / entry) * 100:.2f}%)",
        f"Stop ban đầu: {_num(stop)} ({_pct(stop / entry - 1)})",
        _exit_line(side, exit_level),
        (f"Jev: {direction} {confidence:.2f} ({'đồng ý' if allowed else 'phản đối'})" if direction
         else "Jev: không trả lời (lỗi khi gọi), tín hiệu luật không đổi"),
    ])


def enqueue_soak_signal(store, *, symbol, market, rule, observation, answers, allowed, now, exit_level) -> None:
    """One outbox message per rule, market and closed bar; a repeated cycle never sends twice."""
    advice = jev_advice(rule, answers)
    message = soak_signal_message(
        symbol=symbol, market=market, observation=observation,
        profile=getattr(rule.parameters, "entry_profile", "setup2_v1"), exit_level=exit_level,
        direction=advice["direction"] and advice["direction"].upper().replace("_", " "), confidence=advice["confidence"],
        allowed=allowed)
    key = f"setup2_signal:{rule.rule_id}:{observation.bar_close.isoformat()}"
    store.enqueue_notification(key, "setup2_signal", message, created_at=now)


def drain(store, notifier, *, now) -> dict | None:
    """Deliver pending outbox messages; a delivery or database error never stops the caller's loop."""
    from intraday.notifications import drain_outbox
    try:
        result = drain_outbox(store, notifier, now=now)
    except Exception as error:
        return {"status": "error", "code": type(error).__name__}
    return result if result["delivered"] or result["failed"] else None


# --- Binance Demo -------------------------------------------------------------------------------

def _is_stop(intent) -> bool:
    return intent.order_type in {"STOP_MARKET", "STOP_LOSS"}


def _is_close(intent) -> bool:
    return (intent.source_id or "").startswith("close:")


def _filled(update) -> bool:
    return update.terminal and update.executed_quantity > 0


def _average(update) -> Decimal | None:
    if update.average_price is not None:
        return update.average_price
    quantity = sum((fill.quantity for fill in update.fills), Decimal(0))
    return sum((fill.quantity * fill.price for fill in update.fills), Decimal(0)) / quantity if quantity else None


def _events(orders):
    """Alerts for one account's journaled orders, oldest first: (key, intent, update, kind, entry)."""
    entries = {}
    for intent, update in orders:
        route = (intent.symbol, intent.market)
        if intent.order_type == "MARKET" and not _is_close(intent) and _filled(update):
            entries[route] = (intent, update)
            yield f"{intent.intent_id}:fill", intent, update, "entry", None
        elif _is_stop(intent) and update.status in {"NEW", "PARTIALLY_FILLED", "FILLED"}:
            yield f"{intent.intent_id}:placed", intent, update, "stop", entries.get(route)
            if _filled(update):
                yield f"{intent.intent_id}:fill", intent, update, "exit", entries.get(route)
        elif _is_close(intent) and _filled(update):
            yield f"{intent.intent_id}:fill", intent, update, "exit", entries.get(route)


def _entry_message(intent, update, stop, exit_level, advice=None) -> str:
    side = 1 if intent.side == "BUY" else -1
    price = _average(update)
    lines = [f"[DEMO] VÀO {intent.symbol} {MARKET_LABEL[intent.market]} {_side_label(side)}",
             f"Khớp: {_num(update.executed_quantity)} @ {_num(price)} ({_num(update.executed_quantity * price)} USDT)"]
    lines.append(f"Stop: {_num(stop)} ({_pct(stop / price - 1)})" if stop is not None else "Stop: chưa xác nhận")
    lines.append(_exit_line(side, exit_level))
    if advice == "unavailable":
        lines.append("Jev (tư vấn): không có câu trả lời")
    elif isinstance(advice, dict):
        try:
            direction = advice["direction"].upper().replace("_", " ")
            lines.append(f"Jev (tư vấn): {direction} {advice['confidence']:.2f}, "
                         + ("đồng ý" if advice["would_allow"] else "phản đối"))
        except (KeyError, TypeError, AttributeError, ValueError):
            pass       # Optional evidence must not prevent a fill notification.
    return "\n".join(lines)


def _stop_message(intent, entry) -> str:
    text = f"[DEMO] STOP {intent.symbol} {MARKET_LABEL[intent.market]}: {_num(intent.stop_price)}"
    price = _average(entry[1]) if entry else None
    return text + (f" ({_pct(intent.stop_price / price - 1)} so với giá vào {_num(price)})" if price else "")


def _exit_message(intent, update, entry) -> str:
    side = -1 if intent.side == "BUY" else 1           # the closing order is opposite to the position
    reason = "chạm stop" if _is_stop(intent) else EXIT_REASONS.get(intent.source_id[6:], intent.source_id[6:])
    price, quantity = _average(update), update.executed_quantity
    lines = [f"[DEMO] THOÁT {intent.symbol} {MARKET_LABEL[intent.market]} {_side_label(side)}: {reason}"]
    entry_price = _average(entry[1]) if entry else None
    lines.append(f"Khớp: {_num(quantity)} @ {_num(price)}"
                 + (f" | giá vào {_num(entry_price)}" if entry_price else ""))
    if intent.market == "perp" and update.fills:
        net = sum((fill.realized_pnl - fill.commission for fill in update.fills), Decimal(0))
        lines.append(f"PnL thực hiện (sàn, đã trừ phí): {net:+.2f} USDT")
    elif entry_price:
        lines.append(f"PnL giá: {side * (price - entry_price) * quantity:+.2f} USDT ({_pct(side * (price / entry_price - 1))})")
    return "\n".join(lines)


def notify_demo(journal, notifier, *, exit_level_for, now, max_attempts=5) -> dict | None:
    """Send each Demo entry, stop placement and exit once. The first call only records what the journal
    already holds, so enabling alerts never replays history. A failed send is retried on later cycles."""
    sent = failed = 0
    try:
        state = journal.notifications()
        if BASELINE not in state:
            journal.record_notifications([BASELINE]+[(account, key) for account, orders in journal.all_orders().items()
                                                      for key, *_ in _events(orders)], now=now, status="baseline")
            return None
        for account, orders in journal.all_orders().items():
            events = list(_events(orders))
            for key, intent, update, kind, entry in events:
                status, attempts = state.get((account, key), (None, 0))
                if status in {"delivered", "baseline"} or attempts >= max_attempts:
                    continue
                also = []
                if kind == "entry":
                    # The entry's first stop goes in the entry alert; later stops are trail moves.
                    stop = next(((k, i) for k, i, u, kd, e in events
                                 if kd == "stop" and e and e[0].intent_id == intent.intent_id), None)
                    try:
                        level = exit_level_for(intent.symbol, intent.market, now)
                    except Exception:
                        level = None
                    try:
                        advice = journal.entry_jev(intent)
                    except Exception:
                        advice = None
                    message = _entry_message(intent, update, stop[1].stop_price if stop else None, level, advice)
                    also = [(account, stop[0])] if stop else []
                elif kind == "stop":
                    message = _stop_message(intent, entry)
                else:
                    message = _exit_message(intent, update, entry)
                try:
                    notifier.send(message)
                except Exception:
                    journal.record_notifications([(account, key)], now=now, status="failed")
                    failed += 1
                    continue
                journal.record_notifications([(account, key)]+also, now=now, status="delivered")
                state.update({item: ("delivered", 0) for item in also})
                sent += 1
    except Exception as error:
        return {"status": "error", "code": type(error).__name__}
    return {"delivered": sent, "failed": failed} if sent or failed else None


def notify_demo_pause(report, notifier, *, alerted) -> bool:
    """Announce a paused portfolio once per pause; returns whether it is announced, so a failed send retries next cycle."""
    if report.get("status") != "paused":
        return False
    if alerted:
        return True
    lines = [f"[DEMO] Runner đang pause: {report.get('reason')}"]
    if report.get("error"):
        lines.append(f"Lỗi: {report['error']}")
    lines.append("Không vào lệnh mới; stop trên sàn giữ nguyên. Kiểm tra rồi activate để mở lại.")
    try:
        notifier.send("\n".join(lines))
    except Exception:
        return False
    return True


def telegram_from_environment(environ=None):
    """The worker's Telegram settings (TELEGRAM_ENABLED, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID); None when off."""
    import os
    from intraday.notifications import TelegramNotifier
    env = os.environ if environ is None else environ
    if env.get("TELEGRAM_ENABLED", "").strip().lower() not in {"1", "true", "yes", "on"}:
        return None
    token, chat = env.get("TELEGRAM_BOT_TOKEN"), env.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        raise ValueError("TELEGRAM_ENABLED requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
    return TelegramNotifier(token=token, chat_id=chat)
