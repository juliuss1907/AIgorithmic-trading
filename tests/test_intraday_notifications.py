from datetime import datetime, timezone

from intraday.notifications import TelegramNotifier, drain_outbox
from intraday.store import IntradayStore
from tests.test_intraday_store import records


NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


def test_fill_creates_one_transactional_notification(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    snapshot, decision, gate, fill = records()

    store.record_tick(snapshot, decision, gate, fill)
    store.record_tick(snapshot, decision, gate, fill)

    pending = store.list_pending_notifications()
    assert len(pending) == 1
    assert pending[0]["delivery_key"] == f"paper_fill:{fill.fill_id}"
    assert "PAPER FILL" in pending[0]["message"]


def test_outbox_delivery_marks_success_without_exposing_token(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    snapshot, decision, gate, fill = records()
    store.record_tick(snapshot, decision, gate, fill)
    calls = []

    notifier = TelegramNotifier(
        token="secret-token",
        chat_id="123",
        send_json=lambda url, payload: calls.append((url, payload)) or {"ok": True},
    )
    result = drain_outbox(store, notifier, now=NOW)

    assert result == {"delivered": 1, "failed": 0}
    assert store.list_pending_notifications() == []
    assert calls[0][1]["chat_id"] == "123"
    assert "secret-token" not in str(store.list_pending_notifications())
