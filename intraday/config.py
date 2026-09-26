"""Environment configuration with intentionally safe v1 defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


APP_DIRECTORY = "aigorithmic-trading"


def default_database_path() -> Path:
    root = os.getenv("XDG_STATE_HOME")
    state_home = Path(root).expanduser() if root else Path.home() / ".local" / "state"
    return state_home / APP_DIRECTORY / "intraday.sqlite3"


def default_backup_directory() -> Path:
    root = os.getenv("XDG_STATE_HOME")
    state_home = Path(root).expanduser() if root else Path.home() / ".local" / "state"
    return state_home / APP_DIRECTORY / "backups"


def default_provider_secrets_path() -> Path:
    root = os.getenv("XDG_CONFIG_HOME")
    config_home = Path(root).expanduser() if root else Path.home() / ".config"
    return config_home / APP_DIRECTORY / "provider-secrets.toml"


def resolve_database_path(database: str | Path | None = None) -> Path:
    if database is not None:
        return Path(database).expanduser()
    configured = os.getenv("INTRADAY_DATABASE")
    return Path(configured).expanduser() if configured else default_database_path()


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
    database: Path = field(default_factory=default_database_path)
    symbol: str = "BTCUSDT"
    initial_equity: float = 10_000.0
    interval_seconds: float = 5.0
    order_book_interval_seconds: float = 15.0
    perp_decision_interval_seconds: float = 30.0
    derivatives_interval_seconds: float = 60.0
    compact_shadow_interval_seconds: float = 900.0
    retrospective_hour_vietnam: int = 9
    provider: str = "stub"
    provider_secrets_file: Path = field(default_factory=default_provider_secrets_path)
    llm_analysis_interval_seconds: float = 3600
    mode: str = "paper"
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8081
    control_token: str | None = None
    operator_read_token: str | None = None
    operator_action_token: str | None = None
    operator_actions_enabled: bool = False
    operator_request_ttl_seconds: int = 300
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
        if self.order_book_interval_seconds < self.interval_seconds:
            raise ValueError("order-book interval must not be faster than risk")
        if self.perp_decision_interval_seconds < self.interval_seconds:
            raise ValueError("Perp decision interval must not be faster than risk")
        if self.derivatives_interval_seconds < self.interval_seconds:
            raise ValueError("derivatives interval must not be faster than risk")
        if self.compact_shadow_interval_seconds < self.perp_decision_interval_seconds:
            raise ValueError("compact shadow interval must not be faster than primary")
        if not 0 <= self.retrospective_hour_vietnam <= 23:
            raise ValueError("retrospective hour must be between 0 and 23")
        if self.provider != "stub":
            raise ValueError("only the stub provider is enabled before soak acceptance")
        if self.llm_analysis_interval_seconds < 300:
            raise ValueError("LLM analysis interval must be at least five minutes")
        if self.mode != "paper":
            raise ValueError("real execution is not implemented")
        if not 1 <= self.dashboard_port <= 65535:
            raise ValueError("invalid dashboard port")
        if (
            self.operator_read_token is not None
            and self.operator_action_token is not None
            and self.operator_read_token == self.operator_action_token
        ):
            raise ValueError("operator read and action tokens must be different")
        if self.operator_actions_enabled and not (
            self.operator_read_token and self.operator_action_token
        ):
            raise ValueError("operator actions require separate read and action tokens")
        if not 60 <= self.operator_request_ttl_seconds <= 900:
            raise ValueError("operator request TTL must be between 60 and 900 seconds")
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
            database=resolve_database_path(database),
            symbol=os.getenv("INTRADAY_SYMBOL", "BTCUSDT"),
            initial_equity=float(os.getenv("INTRADAY_PAPER_BALANCE", "10000")),
            interval_seconds=float(os.getenv("INTRADAY_INTERVAL_SECONDS", "5")),
            order_book_interval_seconds=float(
                os.getenv("INTRADAY_ORDER_BOOK_INTERVAL_SECONDS", "15")
            ),
            perp_decision_interval_seconds=float(
                os.getenv("INTRADAY_PERP_DECISION_INTERVAL_SECONDS", "30")
            ),
            derivatives_interval_seconds=float(
                os.getenv("INTRADAY_DERIVATIVES_INTERVAL_SECONDS", "60")
            ),
            compact_shadow_interval_seconds=float(
                os.getenv("INTRADAY_COMPACT_SHADOW_INTERVAL_SECONDS", "900")
            ),
            retrospective_hour_vietnam=int(
                os.getenv("INTRADAY_RETROSPECTIVE_HOUR_VIETNAM", "9")
            ),
            provider=os.getenv("INTRADAY_PROVIDER", "stub"),
            provider_secrets_file=Path(
                os.getenv("INTRADAY_PROVIDER_SECRETS_FILE")
                or default_provider_secrets_path()
            ).expanduser(),
            llm_analysis_interval_seconds=float(
                os.getenv("INTRADAY_LLM_ANALYSIS_INTERVAL", "3600")
            ),
            mode=os.getenv("INTRADAY_MODE", "paper"),
            dashboard_host=os.getenv("INTRADAY_DASHBOARD_HOST", "127.0.0.1"),
            dashboard_port=int(os.getenv("INTRADAY_DASHBOARD_PORT", "8081")),
            control_token=os.getenv("INTRADAY_CONTROL_TOKEN") or None,
            operator_read_token=os.getenv("INTRADAY_OPERATOR_READ_TOKEN") or None,
            operator_action_token=os.getenv("INTRADAY_OPERATOR_ACTION_TOKEN") or None,
            operator_actions_enabled=_boolean(
                "INTRADAY_OPERATOR_ACTIONS_ENABLED", False
            ),
            operator_request_ttl_seconds=int(
                os.getenv("INTRADAY_OPERATOR_REQUEST_TTL_SECONDS", "300")
            ),
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

    @property
    def risk_interval_seconds(self) -> float:
        """Named alias preserving INTRADAY_INTERVAL_SECONDS compatibility."""
        return self.interval_seconds
