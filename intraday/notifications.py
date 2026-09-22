"""Telegram delivery from a transactional, deduplicated outbox."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Callable
from urllib.request import Request, urlopen

from intraday.store import IntradayStore


class TelegramNotifier:
    def __init__(
        self,
        *,
        token: str,
        chat_id: str,
        send_json: Callable[[str, dict], dict] | None = None,
        timeout_seconds: float = 10,
    ):
        if not token or not chat_id:
            raise ValueError("Telegram token and chat id are required")
        self._url = f"https://api.telegram.org/bot{token}/sendMessage"
        self._chat_id = chat_id
        self._timeout_seconds = timeout_seconds
        self._send_json = send_json or self._http_send

    def _http_send(self, url: str, payload: dict) -> dict:
        request = Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=self._timeout_seconds) as response:
            return json.load(response)

    def send(self, message: str) -> None:
        response = self._send_json(
            self._url,
            {"chat_id": self._chat_id, "text": message, "disable_web_page_preview": True},
        )
        if not response.get("ok"):
            raise RuntimeError("Telegram rejected the message")


def drain_outbox(
    store: IntradayStore,
    notifier: TelegramNotifier,
    *,
    now: datetime | None = None,
    limit: int = 100,
) -> dict[str, int]:
    now = now or datetime.now(timezone.utc)
    delivered = failed = 0
    for item in store.list_pending_notifications(limit):
        try:
            notifier.send(item["message"])
        except Exception as error:
            store.mark_notification_failed(item["id"], error_type=type(error).__name__)
            failed += 1
        else:
            store.mark_notification_delivered(item["id"], delivered_at=now)
            delivered += 1
    return {"delivered": delivered, "failed": failed}
