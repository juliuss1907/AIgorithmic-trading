from datetime import datetime, timedelta, timezone

from intraday.hyperliquid import HyperliquidFeed, HyperliquidPublicClient, l2_book_from_message


NOW = datetime(2026, 9, 22, 12, 0, 5, tzinfo=timezone.utc)


def metadata_response():
    return [
        {"universe": [{"name": "ETH"}, {"name": "BTC"}]},
        [
            {
                "funding": "0.00002", "openInterest": "2000",
                "oraclePx": "4000", "markPx": "4001", "midPx": "4000.5",
            },
            {
                "funding": "0.0000125", "openInterest": "1000",
                "oraclePx": "100000", "markPx": "100005", "midPx": "100000",
            },
        ],
    ]


def book():
    return {
        "coin": "BTC",
        "time": int(NOW.timestamp() * 1000),
        "levels": [
            [{"px": "99990", "sz": "1", "n": 1}],
            [{"px": "100010", "sz": "1", "n": 1}],
        ],
    }


def test_public_client_selects_btc_context_from_meta_response():
    calls = []

    def fetch(payload):
        calls.append(payload)
        return metadata_response()

    context = HyperliquidPublicClient(fetch_json=fetch).asset_context()

    assert calls == [{"type": "metaAndAssetCtxs"}]
    assert context["markPx"] == "100005"


def test_public_client_requests_public_btc_l2_snapshot():
    calls = []
    client = HyperliquidPublicClient(
        fetch_json=lambda payload: calls.append(payload) or book()
    )

    assert client.order_book() == book()
    assert calls == [{"type": "l2Book", "coin": "BTC"}]


def test_l2_websocket_parser_accepts_only_btc_book_updates():
    assert l2_book_from_message({"channel": "l2Book", "data": book()}) == book()
    assert l2_book_from_message({"channel": "trades", "data": []}) is None
    assert l2_book_from_message({"channel": "l2Book", "data": {**book(), "coin": "ETH"}}) is None


def test_feed_emits_frame_only_when_book_and_metadata_are_fresh():
    client = HyperliquidPublicClient(fetch_json=lambda payload: metadata_response())
    feed = HyperliquidFeed(client, metadata_interval_seconds=30)
    feed.update_context(client.asset_context(), received_at=NOW)
    feed.update_book(book(), received_at=NOW)

    frame = feed.latest_frame(now=NOW)

    assert frame is not None
    assert frame.mark_price == 100_005
    assert feed.latest_frame(now=NOW + timedelta(seconds=3)) is None

    feed.update_book(book(), received_at=NOW + timedelta(seconds=3))
    assert feed.latest_frame(now=NOW + timedelta(seconds=61)) is None


def test_feed_rejects_old_exchange_event_even_when_just_received():
    client = HyperliquidPublicClient(fetch_json=lambda payload: metadata_response())
    feed = HyperliquidFeed(client, max_book_age_seconds=2)
    stale_book = {
        **book(),
        "time": int((NOW - timedelta(seconds=3)).timestamp() * 1000),
    }
    feed.update_context(client.asset_context(), received_at=NOW)
    feed.update_book(stale_book, received_at=NOW)

    assert feed.latest_frame(now=NOW) is None
