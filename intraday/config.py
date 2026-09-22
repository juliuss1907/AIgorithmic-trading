"""Environment configuration with intentionally safe v1 defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _boolean(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized not in {"true", "false"}:
        raise ValueError(f"{name} must be true or false")
    return normalized == "true"


@dataclass(frozen=True)
class IntradayConfig:
    database: Path = Path("state/intraday/intraday.sqlite3")
    symbol: str = "BTCUSDT"
    initial_equity: float = 10_000.0
    interval_seconds: float = 5.0
    provider: str = "stub"
    mode: str = "paper"
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8081
    control_token: str | None = None
    news_enabled: bool = True
    news_interval_seconds: float = 1800
    cross_venue_mode: str = "shadow"
    hyperliquid_enabled: bool = True
    hyperliquid_metadata_interval_seconds: float = 30
    telegram_enabled: bool = False
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None

    def __post_init__(self):
        if self.symbol != "BTCUSDT":
            raise ValueError("v1 only supports BTCUSDT")
        if self.initial_equity <= 0:
            raise ValueError("initial equity must be positive")
        if self.interval_seconds < 1:
            raise ValueError("interval must be at least one second")
        if self.provider != "stub":
            raise ValueError("only the stub provider is enabled before soak acceptance")
        if self.mode != "paper":
            raise ValueError("real execution is not implemented")
        if not 1 <= self.dashboard_port <= 65535:
            raise ValueError("invalid dashboard port")
        if self.news_interval_seconds < 300:
            raise ValueError("news interval must be at least five minutes")
        if self.cross_venue_mode not in {"off", "shadow", "active"}:
            raise ValueError("cross-venue mode must be off, shadow, or active")
        if self.hyperliquid_metadata_interval_seconds < 10:
            raise ValueError("Hyperliquid metadata interval must be at least ten seconds")
        if self.telegram_enabled and not (self.telegram_bot_token and self.telegram_chat_id):
            raise ValueError("Telegram is enabled but credentials are incomplete")

    @classmethod
    def from_environment(cls, *, database: str | Path | None = None) -> "IntradayConfig":
        return cls(
            database=Path(database or os.getenv("INTRADAY_DATABASE", cls.database)),
            symbol=os.getenv("INTRADAY_SYMBOL", "BTCUSDT"),
            initial_equity=float(os.getenv("INTRADAY_PAPER_BALANCE", "10000")),
            interval_seconds=float(os.getenv("INTRADAY_INTERVAL_SECONDS", "5")),
            provider=os.getenv("INTRADAY_PROVIDER", "stub"),
            mode=os.getenv("INTRADAY_MODE", "paper"),
            dashboard_host=os.getenv("INTRADAY_DASHBOARD_HOST", "127.0.0.1"),
            dashboard_port=int(os.getenv("INTRADAY_DASHBOARD_PORT", "8081")),
            control_token=os.getenv("INTRADAY_CONTROL_TOKEN") or None,
            news_enabled=_boolean("INTRADAY_NEWS_ENABLED", True),
            news_interval_seconds=float(os.getenv("INTRADAY_NEWS_INTERVAL_SECONDS", "1800")),
            cross_venue_mode=os.getenv("INTRADAY_CROSS_VENUE_MODE", "shadow"),
            hyperliquid_enabled=_boolean("INTRADAY_HYPERLIQUID_ENABLED", True),
            hyperliquid_metadata_interval_seconds=float(
                os.getenv("INTRADAY_HYPERLIQUID_METADATA_INTERVAL", "30")
            ),
            telegram_enabled=_boolean("TELEGRAM_ENABLED", False),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID") or None,
        )
