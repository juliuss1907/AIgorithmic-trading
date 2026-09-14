"""Explicit Yahoo snapshot: raw OHLC + adjustment factor, validated session dates."""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

import exchange_calendars as xcals
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OHLC = ["open", "high", "low", "close"]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sessions(start, end):
    return xcals.get_calendar("XNYS", start=start, end=end).sessions.tz_localize(None)


def validate(frame, start, end):
    """Reject incomplete inputs rather than filling missing trading sessions."""
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is not None:
        raise ValueError("Expected timezone-naive session dates")
    if not frame.index.is_unique or not frame.index.is_monotonic_increasing:
        raise ValueError("Duplicate or unsorted sessions")
    expected = sessions(start, end)
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


def _download_yahoo(config):
    import yfinance as yf

    return yf.Ticker(config.symbol).history(
        start=str(config.data.start), end=str(config.data.end_exclusive),
        interval="1d", auto_adjust=False, actions=True, repair=False,
        raise_errors=True, timeout=30,
    )


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


def save_snapshot(raw, config, catalog=None, retrieved_at_utc=None):
    """Validate and persist a frame, publishing it to the catalog only when complete."""
    from lab.datasets import DatasetCatalog

    catalog = catalog or DatasetCatalog()
    raw = _normalize(raw)
    end = str(config.data.end_exclusive - timedelta(days=1))
    validate(raw, str(config.data.start), end)
    adjusted = adjust(raw)
    validate(adjusted, str(config.data.start), end)
    raw_content = _csv_bytes(raw)
    adjusted_content = _csv_bytes(adjusted)
    raw_hash = hashlib.sha256(raw_content).hexdigest()
    adjusted_hash = hashlib.sha256(adjusted_content).hexdigest()
    manifest = {
        "source": "Yahoo Finance via yfinance", "symbol": config.symbol,
        "retrieved_at_utc": retrieved_at_utc or datetime.now(timezone.utc).isoformat(),
        "start": str(config.data.start), "end": end, "rows": len(raw),
        "adjustment": "OHLC * (Adj Close / Close); synthetic total-return prices",
        "dividends": "Implicit in adjusted prices; never credited a second time",
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
    downloader = downloader or _download_yahoo
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
                   if str(item.start) == str(config.data.start) and str(item.end) == expected_end]
        if len(matches) != 1:
            raise ValueError("Select dataset_id explicitly when no unique snapshot matches")
        snapshot = matches[0]
    if snapshot.symbol != config.symbol:
        raise ValueError("Snapshot symbol differs from experiment")
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
