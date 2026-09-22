from datetime import datetime, timedelta, timezone

from intraday.contracts import NewsEvent, NewsSeverity, SourceTier
from intraday.news import NewsIntelligence


NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


def event(source, tier, title, *, event_id, severity=NewsSeverity.HIGH, url=None):
    return NewsEvent(
        event_id=event_id,
        source_id=source,
        source_tier=tier,
        title=title,
        url=url or f"https://{source}.example/{event_id}?utm_source=test",
        published_at=NOW,
        received_at=NOW,
        category="exchange_incident",
        severity=severity,
    )


def test_tier_a_event_is_verified_and_pauses_entries_for_30_minutes():
    intelligence = NewsIntelligence()

    result = intelligence.ingest([
        event("binance", SourceTier.A, "Binance pauses BTCUSDT futures", event_id="official")
    ], now=NOW)

    assert result.clusters[0].verified is True
    assert result.pause_until == NOW + timedelta(minutes=30)


def test_two_independent_sources_need_at_least_one_tier_b():
    intelligence = NewsIntelligence()
    title = "Major stablecoin loses its dollar peg after reserve incident"

    unverified = intelligence.ingest([
        event("wu", SourceTier.C, title, event_id="wu"),
        event("cointelegraph", SourceTier.C, title, event_id="ct"),
    ], now=NOW)
    verified = intelligence.ingest([
        event("coindesk", SourceTier.B, title, event_id="cd"),
    ], now=NOW + timedelta(minutes=1))

    assert unverified.clusters[0].verified is False
    assert unverified.pause_until is None
    assert verified.clusters[0].verified is True
    assert verified.pause_until == NOW + timedelta(minutes=31)


def test_duplicate_urls_and_same_publisher_do_not_fake_confirmation():
    intelligence = NewsIntelligence()
    first = event(
        "coindesk", SourceTier.B, "Protocol exploit drains treasury", event_id="one",
        url="https://coindesk.example/story?utm_source=a",
    )
    duplicate = event(
        "coindesk", SourceTier.B, "Protocol exploit drains treasury", event_id="two",
        url="https://coindesk.example/story?utm_source=b",
    )

    result = intelligence.ingest([first, duplicate], now=NOW)

    assert len(result.accepted_events) == 1
    assert result.clusters[0].verified is False
    assert result.pause_until is None


def test_new_verified_event_extends_but_never_shortens_pause():
    intelligence = NewsIntelligence()
    first = intelligence.ingest([
        event("binance", SourceTier.A, "Binance futures disruption", event_id="one")
    ], now=NOW)
    second = intelligence.ingest([
        event("sec", SourceTier.A, "Emergency enforcement action affects BTC venue", event_id="two")
    ], now=NOW + timedelta(minutes=20))

    assert first.pause_until == NOW + timedelta(minutes=30)
    assert second.pause_until == NOW + timedelta(minutes=50)


def test_old_headline_is_stored_but_does_not_trigger_a_new_pause():
    intelligence = NewsIntelligence()
    old = event("sec", SourceTier.A, "Emergency enforcement action", event_id="old")
    old = old.model_copy(update={"published_at": NOW - timedelta(days=1)})

    result = intelligence.ingest([old], now=NOW)

    assert tuple(item.event_id for item in result.accepted_events) == (old.event_id,)
    assert result.pause_until is None


def test_seeded_history_can_be_confirmed_by_a_later_independent_source():
    title = "Major stablecoin loses its dollar peg after reserve incident"
    prior = event("coindesk", SourceTier.B, title, event_id="prior")
    intelligence = NewsIntelligence(seed_events=[prior])

    result = intelligence.ingest(
        [event("wu", SourceTier.C, title, event_id="later")],
        now=NOW,
    )

    assert result.clusters[0].verified is True
    assert result.pause_until == NOW + timedelta(minutes=30)
