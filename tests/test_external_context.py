from datetime import datetime, timedelta, timezone

import pytest

from intraday.contracts import ExternalObservation
from intraday.external_context import (
    VariationalCollector,
    parse_aster_snapshot,
    parse_cryptorank_context,
    parse_lighter_market_stats,
    parse_variational_stats,
    run_external_context_cycle,
)
from intraday.store import IntradayStore


NOW = datetime(2026, 9, 27, 6, 0, tzinfo=timezone.utc)


def test_external_observation_is_content_addressed_and_strict():
    observation = ExternalObservation.create(
        source="cryptorank",
        dataset="market_context",
        symbol=None,
        source_timestamp=NOW,
        received_at=NOW,
        metrics={"fear_greed_value": 42.0},
        labels={"fear_greed_classification": "Fear"},
    )

    assert observation.observation_id == observation.checksum[:24]
    with pytest.raises(ValueError, match="checksum"):
        ExternalObservation.model_validate(
            {**observation.model_dump(), "metrics": {"fear_greed_value": 99.0}}
        )


def test_store_round_trips_observations_idempotently(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    observation = ExternalObservation.create(
        source="variational",
        dataset="perp_market",
        symbol="BTCUSDT",
        source_timestamp=NOW,
        received_at=NOW,
        metrics={"mark_price": 100_000.0},
        labels={},
    )

    store.record_external_observation(observation)
    store.record_external_observation(observation)

    assert store.list_external_observations(source="variational") == [observation]
    health = store.external_source_health("variational", now=NOW)
    assert health["total_observations"] == 1
    assert health["age_seconds"] == 0


def test_source_health_can_be_scoped_to_one_asset(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    for symbol in ("BTCUSDT", "ETHUSDT"):
        store.record_external_observation(ExternalObservation.create(
            source="variational", dataset="perp_market", symbol=symbol,
            source_timestamp=NOW, received_at=NOW,
            metrics={"mark_price": 1.0}, labels={},
        ))

    health = store.external_source_health(
        "variational", symbol="ETHUSDT", now=NOW
    )

    assert health["symbol"] == "ETHUSDT"
    assert health["total_observations"] == 1
    assert health["latest"]["symbol"] == "ETHUSDT"


def test_external_observation_retention_removes_only_expired_rows(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    old = ExternalObservation.create(
        source="aster", dataset="perp_market", symbol="BTCUSDT",
        source_timestamp=NOW - timedelta(days=31), received_at=NOW - timedelta(days=31),
        metrics={"mark_price": 90_000.0}, labels={},
    )
    current = ExternalObservation.create(
        source="aster", dataset="perp_market", symbol="BTCUSDT",
        source_timestamp=NOW, received_at=NOW,
        metrics={"mark_price": 100_000.0}, labels={},
    )
    store.record_external_observation(old)
    store.record_external_observation(current)

    assert store.prune_external_observations(before=NOW - timedelta(days=30)) == 1
    assert store.list_external_observations(source="aster") == [current]


def test_cryptorank_parser_combines_slow_context():
    result = parse_cryptorank_context(
        fear_greed={"data": {"currentValue": 42, "classification": "Fear"}, "status": {"timestamp": 1790488500000}},
        altcoin={"data": {"currentValue": 61, "classification": "Altcoin Season"}},
        global_market={"data": {"totalMarketCap": "2400000000000", "totalVolume24h": "90000000000", "marketCapChangePercent24h": -1.2}},
        received_at=NOW,
    )

    assert result.metrics["fear_greed_value"] == 42
    assert result.metrics["altcoin_index_value"] == 61
    assert result.metrics["total_market_cap"] == 2_400_000_000_000
    assert result.labels["fear_greed_classification"] == "Fear"


def test_variational_parser_preserves_size_dependent_liquidity():
    result = parse_variational_stats({
        "tvl": "100000000",
        "listings": [{
            "ticker": "BTC", "mark_price": "100000", "volume_24h": "123",
            "open_interest": {"long_open_interest": "110", "short_open_interest": "90"},
            "funding_rate": "0.0002", "funding_interval_s": 28800,
            "base_spread_bps": "0.5",
            "quotes": {"updated_at": "2026-09-27T05:59:00Z", "size_1k": {"bid": "99995", "ask": "100005"}, "size_100k": {"bid": "99950", "ask": "100050"}, "size_1m": {"bid": "99800", "ask": "100200"}},
        }],
    }, received_at=NOW)

    assert result.metrics["long_open_interest_usd"] == 110
    assert result.metrics["short_open_interest_usd"] == 90
    assert result.metrics["quote_spread_1m_bps"] == pytest.approx(40)
    assert result.source_timestamp.isoformat() == "2026-09-27T05:59:00+00:00"


def test_variational_parser_selects_registered_asset():
    result = parse_variational_stats({
        "tvl": "100000000",
        "listings": [{
            "ticker": "ETH", "mark_price": "4000", "volume_24h": "123",
            "open_interest": {"long_open_interest": "110", "short_open_interest": "90"},
            "funding_rate": "0.0002", "funding_interval_s": 28800,
            "base_spread_bps": "0.5",
            "quotes": {"updated_at": "2026-09-27T05:59:00Z", "size_1k": {"bid": "3999", "ask": "4001"}},
        }],
    }, symbol="ETHUSDT", received_at=NOW)

    assert result.symbol == "ETHUSDT"
    assert result.metrics["mark_price"] == 4000


def test_aster_parser_normalizes_basis_funding_book_and_liquidation():
    result = parse_aster_snapshot(
        premium={"markPrice": "100100", "indexPrice": "100000", "lastFundingRate": "0.0008", "time": 1790488740000},
        book={"E": 1790488740000, "bids": [["100090", "2"]], "asks": [["100110", "3"]]},
        liquidation={"E": 1790488739000, "o": {"s": "BTCUSDT", "S": "SELL", "q": "0.5", "ap": "100000"}},
        received_at=NOW,
    )

    assert result.metrics["basis_bps"] == pytest.approx(10)
    assert result.metrics["funding_rate"] == pytest.approx(0.0008)
    assert result.metrics["liquidation_notional_usd"] == 50_000
    assert result.labels["liquidation_side"] == "SELL"


def test_aster_parser_preserves_registered_asset_identity():
    result = parse_aster_snapshot(
        symbol="SOLUSDT",
        premium={"symbol": "SOLUSDT", "markPrice": "200", "indexPrice": "199", "lastFundingRate": "0.0001"},
        book={"bids": [["199.9", "2"]], "asks": [["200.1", "3"]]},
        liquidation=None,
        received_at=NOW,
    )

    assert result.symbol == "SOLUSDT"


def test_aster_parser_drops_stale_liquidation_from_current_snapshot():
    result = parse_aster_snapshot(
        premium={"markPrice": "100100", "indexPrice": "100000", "lastFundingRate": "0.0008", "time": int(NOW.timestamp() * 1000)},
        book={"E": int(NOW.timestamp() * 1000), "bids": [["100090", "2"]], "asks": [["100110", "3"]]},
        liquidation={"E": int((NOW - timedelta(minutes=10)).timestamp() * 1000), "o": {"s": "BTCUSDT", "S": "SELL", "q": "0.5", "ap": "100000"}},
        received_at=NOW,
    )

    assert result.metrics["liquidation_notional_usd"] is None
    assert "liquidation_side" not in result.labels


def test_lighter_parser_normalizes_public_market_stats():
    result = parse_lighter_market_stats({
        "market_stats": {
            "symbol": "BTC", "market_id": 1, "index_price": "100000",
            "mark_price": "100020", "best_bid_price": "100010", "best_ask_price": "100030",
            "open_interest": "50", "current_funding_rate": "0.0003",
            "funding_rate": "0.0002", "funding_timestamp": 1790485200000,
            "daily_quote_token_volume": 90000000, "premium": "0.02",
        },
        "timestamp": 1790488740000,
    }, received_at=NOW)

    assert result.metrics["open_interest_base"] == 50
    assert result.metrics["open_interest_usd"] == 5_001_000
    assert result.metrics["estimated_funding_rate"] == pytest.approx(0.0003)
    assert result.metrics["spread_bps"] == pytest.approx(1.99960008)


def test_lighter_parser_checks_registry_market_mapping():
    result = parse_lighter_market_stats({
        "market_stats": {
            "symbol": "HYPE", "market_id": 24, "index_price": "50",
            "mark_price": "50", "best_bid_price": "49.9", "best_ask_price": "50.1",
            "open_interest": "10", "current_funding_rate": "0.0003",
            "funding_rate": "0.0002", "daily_quote_token_volume": 1000,
            "premium": "0.01",
        },
    }, symbol="HYPEUSDT", received_at=NOW)

    assert result.symbol == "HYPEUSDT"
    assert result.metrics["market_id"] == 24


def test_cycle_isolates_provider_failures_and_persists_success(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    successful = ExternalObservation.create(
        source="variational", dataset="perp_market", symbol="BTCUSDT",
        source_timestamp=NOW, received_at=NOW, metrics={"mark_price": 100000.0}, labels={},
    )

    result = run_external_context_cycle(
        store,
        collectors={"variational": lambda now: successful, "aster": lambda now: (_ for _ in ()).throw(RuntimeError("down"))},
        now=NOW,
    )

    assert result == {"recorded": ["variational"], "failed": ["aster"]}
    assert store.list_external_observations(source="variational") == [successful]


def test_cycle_records_batch_results_per_asset(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    observations = tuple(
        ExternalObservation.create(
            source="variational", dataset="perp_market", symbol=symbol,
            source_timestamp=NOW, received_at=NOW,
            metrics={"mark_price": price}, labels={},
        )
        for symbol, price in (("BTCUSDT", 100000.0), ("ETHUSDT", 4000.0))
    )

    result = run_external_context_cycle(
        store, collectors={"variational": lambda now: observations}, now=NOW
    )

    assert result == {"recorded": ["variational"], "failed": []}
    assert sorted(item.symbol for item in store.list_external_observations(
        source="variational"
    )) == ["BTCUSDT", "ETHUSDT"]


def test_variational_batch_isolates_one_missing_asset(tmp_path):
    store = IntradayStore(tmp_path / "intraday.sqlite")
    collector = VariationalCollector(
        fetch_json=lambda url: {
            "tvl": "100000000",
            "listings": [{
                "ticker": "BTC", "mark_price": "100000", "volume_24h": "123",
                "open_interest": {"long_open_interest": "110", "short_open_interest": "90"},
                "funding_rate": "0.0002", "funding_interval_s": 28800,
                "base_spread_bps": "0.5", "quotes": {
                    "updated_at": "2026-09-27T05:59:00Z",
                    "size_1k": {"bid": "99995", "ask": "100005"},
                },
            }],
        },
        symbols=("BTCUSDT", "ETHUSDT"),
    )

    result = run_external_context_cycle(
        store, collectors={"variational": collector}, now=NOW
    )

    assert result == {
        "recorded": ["variational"],
        "failed": [],
        "partial": {"variational": ["ETHUSDT"]},
    }
    assert [item.symbol for item in store.list_external_observations(
        source="variational"
    )] == ["BTCUSDT"]
