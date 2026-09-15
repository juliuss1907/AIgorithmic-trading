"""Explicit Yahoo snapshot: raw OHLC + adjustment factor, validated session dates."""

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import exchange_calendars as xcals
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OHLC = ["open", "high", "low", "close"]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sessions(start, end, calendar="XNYS"):
    if calendar == "UTC_24_7":
        return pd.date_range(start, end, freq="D")
    if calendar != "XNYS":
        raise ValueError(f"Unsupported calendar: {calendar}")
    return xcals.get_calendar("XNYS", start=start, end=end).sessions.tz_localize(None)


def validate(frame, start, end, calendar="XNYS"):
    """Reject incomplete inputs rather than filling missing trading sessions."""
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is not None:
        raise ValueError("Expected timezone-naive session dates")
    if not frame.index.is_unique or not frame.index.is_monotonic_increasing:
        raise ValueError("Duplicate or unsorted sessions")
    expected = sessions(start, end, calendar=calendar)
    if not frame.index.equals(expected):
        missing = expected.difference(frame.index).strftime("%Y-%m-%d").tolist()
        extra = frame.index.difference(expected).strftime("%Y-%m-%d").tolist()
        raise ValueError(f"Session mismatch: missing={missing}, extra={extra}")
    values = frame[OHLC + ["volume"]].to_numpy(dtype=float)
    if not np.isfinite(values).all() or (frame[OHLC] <= 0).any().any():
        raise ValueError("Non-finite values or non-positive prices")
    if (frame["volume"] < 0).any():
        raise ValueError("Negative volume")
    if ((frame["high"] < frame[OHLC].max(axis=1)) |
            (frame["low"] > frame[OHLC].min(axis=1))).any():
        raise ValueError("Invalid OHLC bounds")


def adjust(raw):
    """Synthetic total-return price units, not historical executable dollar quotes."""
    factor = raw["adj_close"] / raw["close"]
    if not np.isfinite(factor).all() or (factor <= 0).any():
        raise ValueError("Invalid adjustment factor")
    result = raw[OHLC + ["volume"]].copy()
    result[OHLC] = result[OHLC].mul(factor, axis=0)
    return result


def _date_spec(config):
    return getattr(config, "data", config)


def _download_yahoo(config):
    import yfinance as yf

    dates = _date_spec(config)
    return yf.Ticker(config.symbol).history(
        start=str(dates.start), end=str(dates.end_exclusive),
        interval="1d", auto_adjust=False, actions=True, repair=False,
        raise_errors=True, timeout=30,
    )


@dataclass(frozen=True)
class DownloadedBars:
    frame: pd.DataFrame
    metadata: dict


class BinanceClient:
    """Allowlisted Binance public-data client; callers cannot supply a URL."""

    base_url = "https://data-api.binance.vision"

    def __init__(self, fetch_json=None, page_limit=1000, sleep=time.sleep):
        self.fetch_json = fetch_json or self._fetch_json
        self.page_limit = page_limit
        self.sleep = sleep

    def _fetch_json(self, path, params):
        request = Request(
            f"{self.base_url}{path}?{urlencode(params)}",
            headers={"Accept": "application/json", "User-Agent": "system-trading-lab/0.2"},
        )
        for attempt in range(3):
            try:
                with urlopen(request, timeout=30) as response:
                    return json.loads(response.read())
            except HTTPError as exc:
                if exc.code not in {418, 429} or attempt == 2:
                    raise
                self.sleep(min(float(exc.headers.get("Retry-After", "1")), 30))
        raise RuntimeError("Binance retry loop ended unexpectedly")

    @staticmethod
    def _exchange_rules(payload, symbol):
        if not isinstance(payload, dict) or payload.get("timezone") != "UTC":
            raise ValueError("Invalid Binance exchangeInfo response")
        matches = [item for item in payload.get("symbols", []) if item.get("symbol") == symbol]
        if len(matches) != 1 or matches[0].get("status") != "TRADING":
            raise ValueError("BTCUSDT is not reported as TRADING")
        instrument = matches[0]
        filters = {item.get("filterType"): item for item in instrument.get("filters", [])}
        lot = filters.get("LOT_SIZE", {})
        notional = filters.get("NOTIONAL") or filters.get("MIN_NOTIONAL") or {}
        step = lot.get("stepSize")
        minimum = notional.get("minNotional")
        if not step or not minimum or float(step) <= 0 or float(minimum) <= 0:
            raise ValueError("Binance exchange rules are incomplete")
        return {
            "base_asset": instrument.get("baseAsset"),
            "quote_asset": instrument.get("quoteAsset"),
            "exchange_rules": {"quantity_step": step, "min_notional": minimum},
        }

    def history(self, config):
        if config.symbol != "BTCUSDT" or config.interval != "1d":
            raise ValueError("Binance client only supports BTCUSDT daily candles")
        if config.end_exclusive > datetime.now(timezone.utc).date():
            raise ValueError("Binance downloads may include closed UTC candles only")
        start_ms = int(pd.Timestamp(config.start, tz="UTC").timestamp() * 1000)
        end_ms = int(pd.Timestamp(config.end_exclusive, tz="UTC").timestamp() * 1000)
        cursor = start_ms
        rows = []
        while cursor < end_ms:
            payload = self.fetch_json("/api/v3/klines", {
                "symbol": config.symbol, "interval": config.interval,
                "startTime": cursor, "endTime": end_ms - 1, "limit": self.page_limit,
            })
            if not isinstance(payload, list):
                raise ValueError("Invalid Binance kline response")
            if not payload:
                break
            for item in payload:
                if not isinstance(item, list) or len(item) < 7:
                    raise ValueError("Malformed Binance kline")
                opened = int(item[0])
                if start_ms <= opened < end_ms:
                    rows.append({
                        "date": pd.to_datetime(opened, unit="ms", utc=True).tz_localize(None),
                        "open": float(item[1]), "high": float(item[2]), "low": float(item[3]),
                        "close": float(item[4]), "adj_close": float(item[4]),
                        "volume": float(item[5]),
                    })
            next_cursor = int(payload[-1][0]) + 86_400_000
            if next_cursor <= cursor:
                raise ValueError("Binance pagination did not advance")
            cursor = next_cursor
            if len(payload) < self.page_limit:
                break
        frame = pd.DataFrame(rows)
        if frame.empty:
            raise ValueError("Binance returned no candles")
        frame = frame.drop_duplicates("date").set_index("date").sort_index()
        rules = self._exchange_rules(
            self.fetch_json("/api/v3/exchangeInfo", {"symbol": config.symbol}), config.symbol
        )
        return DownloadedBars(frame, rules)


def _download_binance(config):
    return BinanceClient().history(config)


def download_market_data(config):
    if getattr(config, "market", "us_equity") == "crypto_spot":
        return _download_binance(config)
    return _download_yahoo(config)


def _normalize(raw):
    raw = raw.copy()
    raw.index = pd.to_datetime(raw.index)
    raw.index = raw.index.tz_localize(None).normalize()
    raw.index.name = "date"
    raw.columns = raw.columns.str.lower().str.replace(" ", "_")
    return raw


def _csv_bytes(frame):
    buffer = StringIO()
    frame.to_csv(buffer, float_format="%.12g")
    return buffer.getvalue().encode()


def _write_once(path, content):
    """Create immutable snapshot files; identical retries are harmless."""
    path = Path(path)
    if path.exists():
        if path.read_bytes() != content:
            raise FileExistsError(f"Refusing to replace immutable dataset file: {path}")
        return
    with path.open("xb") as stream:
        stream.write(content)


def save_snapshot(raw, config, catalog=None, retrieved_at_utc=None, metadata=None):
    """Validate and persist a frame, publishing it to the catalog only when complete."""
    from lab.datasets import DatasetCatalog

    catalog = catalog or DatasetCatalog()
    if isinstance(raw, DownloadedBars):
        metadata = raw.metadata
        raw = raw.frame
    metadata = metadata or {}
    raw = _normalize(raw)
    dates = _date_spec(config)
    end = str(dates.end_exclusive - timedelta(days=1))
    calendar = getattr(config, "calendar", "XNYS")
    validate(raw, str(dates.start), end, calendar=calendar)
    adjusted = adjust(raw)
    validate(adjusted, str(dates.start), end, calendar=calendar)
    raw_content = _csv_bytes(raw)
    adjusted_content = _csv_bytes(adjusted)
    raw_hash = hashlib.sha256(raw_content).hexdigest()
    adjusted_hash = hashlib.sha256(adjusted_content).hexdigest()
    is_crypto = getattr(config, "market", "us_equity") == "crypto_spot"
    manifest = {
        "identity_version": 2,
        "source": "Binance Spot public REST API" if is_crypto else "Yahoo Finance via yfinance",
        "market": getattr(config, "market", "us_equity"),
        "venue": getattr(config, "venue", "yahoo"),
        "interval": getattr(config, "interval", "1d"),
        "calendar": calendar,
        "base_asset": metadata.get("base_asset", "BTC" if is_crypto else None),
        "quote_asset": metadata.get("quote_asset", "USDT" if is_crypto else "USD"),
        "price_semantics": (
            "Binance BTCUSDT spot candles; no dividends or adjustment"
            if is_crypto else "OHLC adjusted to synthetic total-return price units"
        ),
        "exchange_rules": metadata.get("exchange_rules", {}),
        "symbol": config.symbol,
        "retrieved_at_utc": retrieved_at_utc or datetime.now(timezone.utc).isoformat(),
        "start": str(dates.start), "end": end, "rows": len(raw),
        "adjustment": (
            "None; adjusted OHLC equals raw OHLC" if is_crypto
            else "OHLC * (Adj Close / Close); synthetic total-return prices"
        ),
        "dividends": (
            "Not applicable to BTCUSDT spot"
            if is_crypto else "Implicit in adjusted prices; never credited a second time"
        ),
        "files": {"raw.csv": raw_hash, "adjusted.csv": adjusted_hash},
    }
    snapshot_id = DatasetCatalog.identity(manifest, raw_hash, adjusted_hash)
    destination = catalog.base_dir / "snapshots" / snapshot_id
    destination.mkdir(parents=True, exist_ok=True)
    _write_once(destination / "raw.csv", raw_content)
    _write_once(destination / "adjusted.csv", adjusted_content)
    manifest_path = destination / "manifest.json"
    if not manifest_path.exists():
        _write_once(manifest_path, (json.dumps(manifest, indent=2) + "\n").encode())
    return catalog.register(manifest_path, destination / "raw.csv", destination / "adjusted.csv")


def fetch(config, catalog=None, downloader=None):
    """The only network path; replay never calls this function."""
    downloader = downloader or download_market_data
    return save_snapshot(downloader(config), config, catalog=catalog)


def resolve_snapshot(config, catalog=None):
    """Resolve one immutable snapshot without reading prices or using the network."""
    from lab.datasets import DatasetCatalog, register_legacy_pilot

    catalog = catalog or DatasetCatalog()
    if config.dataset_id:
        snapshot = catalog.get(config.dataset_id)
    else:
        legacy_manifest = catalog.base_dir / "manifest.json"
        if legacy_manifest.exists():
            register_legacy_pilot(catalog)
        expected_end = str(config.data.end_exclusive - timedelta(days=1))
        matches = [item for item in catalog.list_ready(config.symbol)
                   if (str(item.start) == str(config.data.start) and str(item.end) == expected_end
                       and item.market == getattr(config, "market", "us_equity")
                       and item.venue == getattr(config, "venue", "yahoo")
                       and item.interval == getattr(config, "interval", "1d"))]
        if len(matches) != 1:
            raise ValueError("Select dataset_id explicitly when no unique snapshot matches")
        snapshot = matches[0]
    if snapshot.symbol != config.symbol:
        raise ValueError("Snapshot symbol differs from experiment")
    if snapshot.market != getattr(config, "market", "us_equity"):
        raise ValueError("Snapshot market differs from experiment")
    if snapshot.venue != getattr(config, "venue", "yahoo"):
        raise ValueError("Snapshot venue differs from experiment")
    if str(snapshot.start) != str(config.data.start):
        raise ValueError("Snapshot start differs from experiment")
    expected_end = str(config.data.end_exclusive - timedelta(days=1))
    if str(snapshot.end) != expected_end:
        raise ValueError("Snapshot end differs from experiment")
    return snapshot


def load(config, catalog=None):
    """Load exactly one registered snapshot without any network fallback."""
    from lab.datasets import DatasetCatalog

    catalog = catalog or DatasetCatalog()
    snapshot = resolve_snapshot(config, catalog)
    frame, manifest = catalog.load(snapshot.id)
    return frame, manifest
