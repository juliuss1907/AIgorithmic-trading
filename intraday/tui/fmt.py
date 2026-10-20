"""Small formatters shared by the `aigt ui` views: times in UTC, percentages, routes, sides."""

from __future__ import annotations

from datetime import datetime, timezone

from rich.text import Text


def when(iso: str | None) -> str:
    """`19/10 12:00Z`; `–` without a time."""
    if not iso:
        return "–"
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc).strftime("%d/%m %H:%MZ")


def ago(iso: str | None, now: datetime) -> str:
    if not iso:
        return "–"
    seconds = (now-datetime.fromisoformat(iso.replace("Z", "+00:00"))).total_seconds()
    if seconds < 0:
        return f"còn {duration(-seconds)}"
    return f"{duration(seconds)} trước"


def duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 90:
        return f"{seconds}s"
    minutes = seconds//60
    if minutes < 90:
        return f"{minutes}p"
    hours = minutes//60
    if hours < 48:
        return f"{hours}h{minutes % 60:02d}"
    return f"{hours//24}d{hours % 24}h"


def price(value: str | float | None) -> str:
    """`2415.01000000` → `2415.01`: six significant digits, no trailing zeros."""
    return "–" if value in (None, "") else f"{float(value):.6g}"


def pct(value: float | None, *, digits: int = 2, signed: bool = True) -> Text:
    """A percentage coloured green above zero and red below; `–` without a value."""
    if value is None:
        return Text("–", style="dim")
    text = f"{value:+.{digits}f}%" if signed else f"{value:.{digits}f}%"
    return Text(text, style="green" if value > 0 else "red" if value < 0 else "")


def route(name: str) -> str:
    """`ETHUSDT:perp` → `ETH Perp`."""
    symbol, _, market = name.partition(":")
    return f"{symbol.removesuffix('USDT')} {market.capitalize()}"


def side(value: int | None) -> Text:
    if value is None:
        return Text("–", style="dim")
    return Text("Long", style="green bold") if value > 0 else Text("Short", style="red bold")


def flag(ok: bool, yes: str = "ổn", no: str = "lỗi") -> Text:
    return Text(f"● {yes}", style="green") if ok else Text(f"● {no}", style="red bold")


def stat(summary: dict | None) -> str:
    """`n đã đóng · TB ±x% (SE y)` for the learning review's outcome summaries."""
    if not summary or not summary.get("signals"):
        return "chưa có tín hiệu"
    mean, error = summary.get("mean_return_pct"), summary.get("standard_error_pct")
    closed = f"{summary['n']}/{summary['signals']} đã đóng"
    if mean is None:
        return closed
    return f"{closed} · TB {mean:+.2f}%" + (f" (SE {error:.2f})" if error is not None else "")
