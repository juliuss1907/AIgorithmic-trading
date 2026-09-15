from datetime import date, timedelta

import pandas as pd
import pytest

from lab.contracts import DatasetRequest
from lab.data import BinanceClient, save_snapshot, sessions, validate
from lab.datasets import DatasetCatalog


def btc_request(start="2017-08-17", end_exclusive="2017-08-20"):
    return DatasetRequest.model_validate({
        "market": "crypto_spot",
        "venue": "binance",
        "symbol": "BTCUSDT",
        "interval": "1d",
        "calendar": "UTC_24_7",
        "start": start,
        "end_exclusive": end_exclusive,
    })


def kline(day, open_price):
    opened = int(pd.Timestamp(day, tz="UTC").timestamp() * 1000)
    return [
        opened, str(open_price), str(open_price + 10), str(open_price - 10),
        str(open_price + 5), "12.5", opened + 86_399_999, "0", 100, "0", "0", "0",
    ]


def test_crypto_calendar_includes_every_utc_day_and_rejects_a_gap():
    index = sessions("2025-01-03", "2025-01-05", calendar="UTC_24_7")
    assert index.tolist() == [
        pd.Timestamp("2025-01-03"), pd.Timestamp("2025-01-04"), pd.Timestamp("2025-01-05")
    ]
    frame = pd.DataFrame(
        {"open": 100, "high": 110, "low": 90, "close": 105, "volume": 1},
        index=index.delete(1),
    )
    with pytest.raises(ValueError, match="2025-01-04"):
        validate(frame, "2025-01-03", "2025-01-05", calendar="UTC_24_7")


def test_binance_client_paginates_and_freezes_exchange_rules():
    rows = [kline("2017-08-17", 4000), kline("2017-08-18", 4100), kline("2017-08-19", 4200)]
    calls = []

    def fetch_json(path, params):
        calls.append((path, params))
        if path == "/api/v3/exchangeInfo":
            return {
                "timezone": "UTC",
                "symbols": [{
                    "symbol": "BTCUSDT", "status": "TRADING", "baseAsset": "BTC",
                    "quoteAsset": "USDT", "filters": [
                        {"filterType": "LOT_SIZE", "stepSize": "0.00001000"},
                        {"filterType": "NOTIONAL", "minNotional": "5.00000000"},
                    ],
                }],
            }
        start = params["startTime"]
        available = [row for row in rows if row[0] >= start]
        return available[:params["limit"]]

    result = BinanceClient(fetch_json=fetch_json, page_limit=2).history(btc_request())

    assert result.frame.index.tolist() == list(pd.date_range("2017-08-17", periods=3))
    assert result.frame.iloc[0]["open"] == 4000
    assert result.frame.iloc[-1]["adj_close"] == 4205
    assert result.metadata["exchange_rules"] == {
        "quantity_step": "0.00001000", "min_notional": "5.00000000"
    }
    assert [path for path, _ in calls].count("/api/v3/klines") == 2


def test_binance_client_refuses_an_open_daily_candle():
    today = date.today()
    request = btc_request(
        start=str(today - timedelta(days=1)),
        end_exclusive=str(today + timedelta(days=1)),
    )

    with pytest.raises(ValueError, match="closed UTC candles"):
        BinanceClient(fetch_json=lambda *_: pytest.fail("network must not be called")).history(request)


def test_crypto_snapshot_records_24_7_semantics_and_replays_without_network(tmp_path):
    request = btc_request()
    index = pd.date_range("2017-08-17", "2017-08-19")
    raw = pd.DataFrame({
        "open": [4000, 4100, 4200], "high": [4010, 4110, 4210],
        "low": [3990, 4090, 4190], "close": [4005, 4105, 4205],
        "adj_close": [4005, 4105, 4205], "volume": [1, 2, 3],
    }, index=index)
    catalog = DatasetCatalog(tmp_path / "data")

    snapshot = save_snapshot(raw, request, catalog=catalog, metadata={
        "exchange_rules": {"quantity_step": "0.00001000", "min_notional": "5.00000000"}
    })
    loaded, manifest = catalog.load(snapshot.id)

    assert snapshot.market == "crypto_spot"
    assert snapshot.calendar == "UTC_24_7"
    assert snapshot.base_asset == "BTC" and snapshot.quote_asset == "USDT"
    assert snapshot.price_semantics == "Binance BTCUSDT spot candles; no dividends or adjustment"
    assert snapshot.exchange_rules["quantity_step"] == "0.00001000"
    assert manifest["identity_version"] == 2
    expected = raw[["open", "high", "low", "close", "volume"]].astype(float)
    expected.index.name = "date"
    pd.testing.assert_frame_equal(loaded, expected, check_freq=False, check_dtype=False)


def test_existing_catalog_rows_migrate_with_equity_defaults(tmp_path):
    catalog = DatasetCatalog(tmp_path / "data")
    with catalog._connect() as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(dataset_snapshots)")}

    assert {"market", "venue", "interval", "calendar", "price_semantics"}.issubset(columns)
