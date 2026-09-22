"""Allowlisted news feeds and deterministic normalization of untrusted RSS data."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from intraday.contracts import NewsEvent, NewsSeverity, SourceTier


@dataclass(frozen=True)
class NewsSource:
    source_id: str
    tier: SourceTier
    url: str | None
    enabled: bool
    disabled_reason: str | None = None


NEWS_SOURCES = {
    "sec": NewsSource("sec", SourceTier.A, "https://www.sec.gov/news/pressreleases.rss", True),
    "cftc": NewsSource(
        "cftc", SourceTier.A, "https://www.cftc.gov/RSS/RSSGP/rssgp.xml", True
    ),
    "fed": NewsSource(
        "fed", SourceTier.A, "https://www.federalreserve.gov/feeds/press_all.xml", True
    ),
    "coindesk": NewsSource(
        "coindesk", SourceTier.B, "https://www.coindesk.com/arc/outboundfeeds/rss", True
    ),
    "decrypt": NewsSource("decrypt", SourceTier.B, "https://decrypt.co/feed", True),
    "cointelegraph": NewsSource(
        "cointelegraph", SourceTier.C, "https://cointelegraph.com/rss", True
    ),
    "binance": NewsSource(
        "binance",
        SourceTier.A,
        None,
        False,
        "announcement websocket requires a separate signed read-only API credential",
    ),
    "the_block": NewsSource(
        "the_block",
        SourceTier.B,
        None,
        False,
        "publisher terms prohibit automated AI/ML processing of its content",
    ),
    "wu_blockchain": NewsSource(
        "wu_blockchain",
        SourceTier.C,
        None,
        False,
        "no stable authorized machine-readable endpoint has been verified",
    ),
}


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _plain_text(value: str, limit: int) -> str:
    parser = _TextExtractor()
    parser.feed(value)
    return " ".join("".join(parser.parts).split())[:limit]


def enabled_sources() -> tuple[NewsSource, ...]:
    return tuple(source for source in NEWS_SOURCES.values() if source.enabled)


def _classification(title: str) -> tuple[str, NewsSeverity]:
    lowered = title.lower()
    critical = {"hack", "exploit", "breach", "emergency", "withdrawal halt", "insolvency"}
    high = {"suspend", "outage", "ban", "enforcement", "lawsuit", "liquidation"}
    if any(word in lowered for word in critical):
        return "security_incident", NewsSeverity.CRITICAL
    if any(word in lowered for word in high):
        return "market_risk", NewsSeverity.HIGH
    if any(word in lowered for word in {"rate", "inflation", "etf", "regulation", "rule"}):
        return "macro_regulatory", NewsSeverity.MEDIUM
    return "general", NewsSeverity.LOW


def _child_text(item, names: tuple[str, ...]) -> str | None:
    for child in item:
        local = child.tag.rsplit("}", 1)[-1]
        if local in names:
            if local == "link" and child.attrib.get("href"):
                return child.attrib["href"]
            if child.text:
                return child.text.strip()
    return None


def parse_feed(data: bytes, source: NewsSource, *, received_at: datetime) -> list[NewsEvent]:
    if not source.enabled or source.url is None:
        raise ValueError("source is not enabled")
    if len(data) > 2_000_000:
        raise ValueError("feed exceeds the two-megabyte input limit")
    root = ElementTree.fromstring(data)
    items = [
        node for node in root.iter()
        if node.tag.rsplit("}", 1)[-1] in {"item", "entry"}
    ][:100]
    events = []
    for item in items:
        title = _plain_text(_child_text(item, ("title",)) or "", 500)
        url = (_child_text(item, ("link",)) or "")[:2048]
        if len(title) < 5 or not url.startswith(("https://", "http://")):
            continue
        identity = _child_text(item, ("guid", "id")) or url
        date_text = _child_text(item, ("pubDate", "published", "updated"))
        try:
            published = parsedate_to_datetime(date_text) if date_text else received_at
        except (TypeError, ValueError):
            try:
                published = datetime.fromisoformat(date_text.replace("Z", "+00:00"))
            except (AttributeError, ValueError):
                published = received_at
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        category, severity = _classification(title)
        summary = _plain_text(
            _child_text(item, ("description", "summary", "content")) or "", 2000
        )
        event_id = hashlib.sha256(f"{source.source_id}:{identity}".encode()).hexdigest()[:32]
        events.append(NewsEvent(
            event_id=event_id,
            source_id=source.source_id,
            source_tier=source.tier,
            title=title,
            url=url,
            published_at=published.astimezone(timezone.utc),
            received_at=received_at,
            category=category,
            severity=severity,
            summary=summary or None,
        ))
    return events


def fetch_source(source: NewsSource, *, received_at: datetime, timeout_seconds: float = 10):
    if not source.enabled or source.url is None:
        raise ValueError("source is not enabled")
    request = Request(
        source.url,
        headers={"User-Agent": "system-trading-lab/0.1 local-paper-research"},
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        data = response.read(2_000_001)
    return parse_feed(data, source, received_at=received_at)
