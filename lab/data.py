"""Explicit Yahoo snapshot: raw OHLC + adjustment factor, validated session dates."""

import hashlib
import json
from datetime import datetime, timezone
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


def fetch(config):
    """Only this command uses the network; existing snapshots are never replaced."""
    if DATA.exists():
        raise FileExistsError("data/ already exists; use the saved snapshot for replay")
    import yfinance as yf

    raw = yf.Ticker(config.symbol).history(
        start=str(config.data.start), end=str(config.data.end_exclusive),
        interval="1d", auto_adjust=False, actions=True, repair=False,
        raise_errors=True, timeout=30,
    )
    raw.index = raw.index.tz_localize(None).normalize()
    raw.index.name = "date"
    raw.columns = raw.columns.str.lower().str.replace(" ", "_")
    end = str(config.data.end_exclusive - pd.Timedelta(days=1))
    validate(raw, str(config.data.start), end)
    adjusted = adjust(raw)
    validate(adjusted, str(config.data.start), end)
    DATA.mkdir()
    raw.to_csv(DATA / "spy-raw.csv", float_format="%.12g")
    adjusted.to_csv(DATA / "spy-adjusted.csv", float_format="%.12g")
    manifest = {
        "source": "Yahoo Finance via yfinance", "symbol": config.symbol,
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "start": str(config.data.start), "end": end, "rows": len(raw),
        "adjustment": "OHLC * (Adj Close / Close); synthetic total-return prices",
        "dividends": "Implicit in adjusted prices; never credited a second time",
        "files": {p.name: digest(p) for p in sorted(DATA.glob("*.csv"))},
    }
    (DATA / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def load(config):
    manifest = json.loads((DATA / "manifest.json").read_text())
    if manifest["symbol"] != config.symbol:
        raise ValueError("Snapshot symbol differs from experiment")
    for name in ("spy-raw.csv", "spy-adjusted.csv"):
        if digest(DATA / name) != manifest["files"][name]:
            raise ValueError(f"Snapshot checksum mismatch: {name}")
    frame = pd.read_csv(DATA / "spy-adjusted.csv", index_col="date", parse_dates=True)
    validate(frame, manifest["start"], manifest["end"])
    if manifest["start"] != str(config.data.start):
        raise ValueError("Snapshot start differs from experiment")
    expected_end = str(config.data.end_exclusive - pd.Timedelta(days=1))
    if manifest["end"] != expected_end:
        raise ValueError("Snapshot end differs from experiment")
    return frame, manifest
