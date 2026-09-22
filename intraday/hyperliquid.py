"""Public, read-only Hyperliquid market-data adapter."""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime, timezone
from typing import Callable
from urllib.request import Request, urlopen

from intraday.contracts import VenueMarketFrame
from intraday.cross_venue import build_hyperliquid_frame


INFO_URL = "https://api.hyperliquid.xyz/info"
WS_URL = "wss://api.hyperliquid.xyz/ws"


def l2_book_from_message(message: dict) -> dict | None:
    if message.get("channel") != "l2Book":
        return None
    data = message.get("data")
    if not isinstance(data, dict) or data.get("coin") != "BTC":
        return None
    return data


class HyperliquidPublicClient:
    def __init__(
        self,
        *,
        fetch_json: Callable[[dict], object] | None = None,
        timeout_seconds: float = 10,
    ):
        self.timeout_seconds = timeout_seconds
        self._fetch_json = fetch_json or self._http_post

    def _http_post(self, payload: dict):
        request = Request(
            INFO_URL,
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "User-Agent": "system-trading-lab/0.1",
            },
            method="POST",
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            return json.load(response)

    def asset_context(self) -> dict:
        response = self._fetch_json({"type": "metaAndAssetCtxs"})
        if not isinstance(response, list) or len(response) != 2:
            raise ValueError("invalid Hyperliquid metadata response")
        meta, contexts = response
        universe = meta.get("universe", [])
        index = next(
            (position for position, asset in enumerate(universe) if asset.get("name") == "BTC"),
            None,
        )
        if index is None or index >= len(contexts):
            raise ValueError("BTC context is missing from Hyperliquid metadata")
        return contexts[index]

    def order_book(self) -> dict:
        response = self._fetch_json({"type": "l2Book", "coin": "BTC"})
        if not isinstance(response, dict) or response.get("coin") != "BTC":
            raise ValueError("invalid Hyperliquid BTC order-book response")
        return response


class HyperliquidFeed:
    """Thread-safe hybrid feed: WebSocket book plus periodic REST context."""

    def __init__(
        self,
        client: HyperliquidPublicClient | None = None,
        *,
        metadata_interval_seconds: float = 30,
        max_book_age_seconds: float = 2,
        max_metadata_age_seconds: float = 60,
    ):
        self.client = client or HyperliquidPublicClient()
        self.metadata_interval_seconds = metadata_interval_seconds
        self.max_book_age_seconds = max_book_age_seconds
        self.max_metadata_age_seconds = max_metadata_age_seconds
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._book: dict | None = None
        self._book_received_at: datetime | None = None
        self._context: dict | None = None
        self._context_received_at: datetime | None = None

    def update_book(self, book: dict, *, received_at: datetime) -> None:
        with self._lock:
            if self._book_received_at is None or received_at >= self._book_received_at:
                self._book = book
                self._book_received_at = received_at

    def update_context(self, context: dict, *, received_at: datetime) -> None:
        with self._lock:
            if self._context_received_at is None or received_at >= self._context_received_at:
                self._context = context
                self._context_received_at = received_at

    def refresh_context(self, *, now: datetime | None = None) -> None:
        self.update_context(
            self.client.asset_context(),
            received_at=now or datetime.now(timezone.utc),
        )

    def latest_frame(self, *, now: datetime | None = None) -> VenueMarketFrame | None:
        now = now or datetime.now(timezone.utc)
        with self._lock:
            book = self._book
            book_time = self._book_received_at
            context = self._context
            context_time = self._context_received_at
        if book is None or book_time is None or context is None or context_time is None:
            return None
        book_age = (now - book_time).total_seconds()
        context_age = (now - context_time).total_seconds()
        if not (-1 <= book_age <= self.max_book_age_seconds):
            return None
        try:
            event_time = datetime.fromtimestamp(
                float(book["time"]) / 1000,
                tz=timezone.utc,
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
        event_age = (now - event_time).total_seconds()
        if not (-1 <= event_age <= self.max_book_age_seconds):
            return None
        if not (-1 <= context_age <= self.max_metadata_age_seconds):
            return None
        return build_hyperliquid_frame(
            book,
            context,
            received_at=book_time,
            metadata_received_at=context_time,
        )

    def _metadata_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.refresh_context()
            except Exception:
                pass
            self._stop.wait(self.metadata_interval_seconds)

    async def _book_loop(self) -> None:
        from websockets.asyncio.client import connect

        backoff = 1.0
        while not self._stop.is_set():
            try:
                async with connect(WS_URL, open_timeout=10, ping_interval=20) as socket:
                    await socket.send(json.dumps({
                        "method": "subscribe",
                        "subscription": {"type": "l2Book", "coin": "BTC"},
                    }))
                    backoff = 1.0
                    async for raw in socket:
                        parsed = l2_book_from_message(json.loads(raw))
                        if parsed is not None:
                            self.update_book(parsed, received_at=datetime.now(timezone.utc))
                        if self._stop.is_set():
                            break
            except Exception:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    def start(self) -> None:
        threading.Thread(target=self._metadata_loop, daemon=True).start()
        threading.Thread(target=lambda: asyncio.run(self._book_loop()), daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
