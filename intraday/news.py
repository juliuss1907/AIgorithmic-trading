"""News normalization, deduplication, verification, and bounded entry pauses."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from intraday.contracts import (
    NewsCluster,
    NewsEvent,
    NewsIngestResult,
    NewsSeverity,
    SourceTier,
)


TRACKING_KEYS = {"fbclid", "gclid", "ref", "source"}


def canonical_url(url: str) -> str:
    parts = urlsplit(url)
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_KEYS
    ]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), urlencode(query), ""))


def title_tokens(title: str) -> frozenset[str]:
    return frozenset(re.findall(r"[a-z0-9]+", title.lower()))


def similar(left: str, right: str) -> bool:
    a, b = title_tokens(left), title_tokens(right)
    if not a or not b:
        return False
    return len(a & b) / len(a | b) >= 0.70


class NewsIntelligence:
    def __init__(
        self,
        pause_minutes: int = 30,
        *,
        seed_events: list[NewsEvent] | None = None,
        pause_until: datetime | None = None,
    ):
        self.pause_duration = timedelta(minutes=pause_minutes)
        self._events: dict[str, NewsEvent] = {}
        self._urls: set[str] = set()
        self._pause_until = pause_until
        for event in seed_events or []:
            normalized = event.model_copy(update={"url": canonical_url(event.url)})
            self._events[event.event_id] = normalized
            self._urls.add(normalized.url)

    @staticmethod
    def _verified(events: list[NewsEvent]) -> bool:
        if any(event.source_tier == SourceTier.A for event in events):
            return True
        independent_sources = {event.source_id for event in events}
        return (
            len(independent_sources) >= 2
            and any(event.source_tier == SourceTier.B for event in events)
        )

    @staticmethod
    def _severity(events: list[NewsEvent]) -> NewsSeverity:
        rank = {
            NewsSeverity.LOW: 0,
            NewsSeverity.MEDIUM: 1,
            NewsSeverity.HIGH: 2,
            NewsSeverity.CRITICAL: 3,
        }
        return max((event.severity for event in events), key=rank.get)

    def _clusters(self) -> tuple[NewsCluster, ...]:
        groups: list[list[NewsEvent]] = []
        for event in sorted(self._events.values(), key=lambda item: (item.received_at, item.event_id)):
            group = next(
                (
                    current for current in groups
                    if current[0].category == event.category and similar(current[0].title, event.title)
                ),
                None,
            )
            if group is None:
                groups.append([event])
            else:
                group.append(event)
        clusters = []
        for events in groups:
            raw = ":".join(sorted(event.event_id for event in events)).encode()
            clusters.append(NewsCluster(
                cluster_id=hashlib.sha256(raw).hexdigest()[:24],
                title=events[0].title,
                category=events[0].category,
                severity=self._severity(events),
                source_ids=tuple(sorted({event.source_id for event in events})),
                event_ids=tuple(sorted(event.event_id for event in events)),
                verified=self._verified(events),
                first_seen_at=min(event.received_at for event in events),
                last_seen_at=max(event.received_at for event in events),
            ))
        return tuple(clusters)

    def ingest(self, events: list[NewsEvent], *, now: datetime) -> NewsIngestResult:
        accepted = []
        for event in events:
            url = canonical_url(event.url)
            if event.event_id in self._events or url in self._urls:
                continue
            self._events[event.event_id] = event.model_copy(update={"url": url})
            self._urls.add(url)
            accepted.append(self._events[event.event_id])

        clusters = self._clusters()
        recent_ids = {
            event.event_id for event in accepted
            if timedelta(0) <= now - event.published_at <= timedelta(hours=2)
        }
        if any(
            cluster.verified and cluster.severity in {NewsSeverity.HIGH, NewsSeverity.CRITICAL}
            and any(event_id in recent_ids for event_id in cluster.event_ids)
            for cluster in clusters
        ):
            proposed = now + self.pause_duration
            if self._pause_until is None or proposed > self._pause_until:
                self._pause_until = proposed
        return NewsIngestResult(
            accepted_events=tuple(accepted),
            clusters=clusters,
            pause_until=self._pause_until,
        )
