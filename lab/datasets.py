"""Content-addressed dataset catalog backed by SQLite."""

import hashlib
import json
import sqlite3
from pathlib import Path

import pandas as pd

from lab.contracts import DatasetSnapshot
from lab.data import DATA, adjust, digest, validate


class DatasetCatalog:
    def __init__(self, base_dir=DATA, database=None):
        self.base_dir = Path(base_dir).resolve()
        self.database = Path(database).resolve() if database else self.base_dir / "catalog.sqlite3"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self):
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS dataset_snapshots (
                    id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    source TEXT NOT NULL,
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL,
                    retrieved_at_utc TEXT NOT NULL,
                    rows INTEGER NOT NULL,
                    adjustment TEXT NOT NULL,
                    raw_path TEXT NOT NULL,
                    adjusted_path TEXT NOT NULL,
                    manifest_path TEXT NOT NULL,
                    raw_sha256 TEXT NOT NULL,
                    adjusted_sha256 TEXT NOT NULL,
                    manifest_sha256 TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status = 'ready'),
                    market TEXT NOT NULL DEFAULT 'us_equity',
                    venue TEXT NOT NULL DEFAULT 'yahoo',
                    interval TEXT NOT NULL DEFAULT '1d',
                    calendar TEXT NOT NULL DEFAULT 'XNYS',
                    base_asset TEXT,
                    quote_asset TEXT,
                    price_semantics TEXT NOT NULL DEFAULT 'synthetic total-return prices',
                    exchange_rules_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(dataset_snapshots)")}
            migrations = {
                "market": "TEXT NOT NULL DEFAULT 'us_equity'",
                "venue": "TEXT NOT NULL DEFAULT 'yahoo'",
                "interval": "TEXT NOT NULL DEFAULT '1d'",
                "calendar": "TEXT NOT NULL DEFAULT 'XNYS'",
                "base_asset": "TEXT",
                "quote_asset": "TEXT",
                "price_semantics": "TEXT NOT NULL DEFAULT 'synthetic total-return prices'",
                "exchange_rules_json": "TEXT NOT NULL DEFAULT '{}'",
            }
            for name, definition in migrations.items():
                if name not in columns:
                    connection.execute(
                        f"ALTER TABLE dataset_snapshots ADD COLUMN {name} {definition}"
                    )

    def _relative(self, path):
        resolved = Path(path).resolve()
        try:
            return str(resolved.relative_to(self.base_dir))
        except ValueError as exc:
            raise ValueError("Dataset files must stay inside the data directory") from exc

    def _resolve(self, relative):
        path = (self.base_dir / relative).resolve()
        try:
            path.relative_to(self.base_dir)
        except ValueError as exc:
            raise ValueError("Unsafe dataset path in catalog") from exc
        return path

    @staticmethod
    def identity(manifest, raw_hash, adjusted_hash):
        identity = {
            "symbol": manifest["symbol"],
            "source": manifest["source"],
            "start": manifest["start"],
            "end": manifest["end"],
            "rows": manifest["rows"],
            "adjustment": manifest["adjustment"],
            "raw_sha256": raw_hash,
            "adjusted_sha256": adjusted_hash,
        }
        if manifest.get("identity_version") == 2:
            identity.update({
                "identity_version": 2,
                "market": manifest["market"], "venue": manifest["venue"],
                "interval": manifest["interval"], "calendar": manifest["calendar"],
                "base_asset": manifest.get("base_asset"),
                "quote_asset": manifest.get("quote_asset"),
                "price_semantics": manifest["price_semantics"],
                "exchange_rules": manifest.get("exchange_rules", {}),
            })
        payload = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(payload).hexdigest()

    def register(self, manifest_path, raw_path, adjusted_path):
        manifest_path = Path(manifest_path)
        raw_path = Path(raw_path)
        adjusted_path = Path(adjusted_path)
        relative_raw = self._relative(raw_path)
        relative_adjusted = self._relative(adjusted_path)
        relative_manifest = self._relative(manifest_path)
        manifest = json.loads(manifest_path.read_text())
        required = {"source", "symbol", "retrieved_at_utc", "start", "end", "rows", "adjustment", "files"}
        if not required.issubset(manifest):
            raise ValueError("Dataset manifest is incomplete")
        if manifest.get("identity_version") == 2:
            versioned = {"market", "venue", "interval", "calendar", "price_semantics"}
            if not versioned.issubset(manifest):
                raise ValueError("Version 2 dataset manifest is incomplete")
        raw_hash = digest(raw_path)
        adjusted_hash = digest(adjusted_path)
        manifest_hash = digest(manifest_path)
        if manifest["files"].get(raw_path.name) != raw_hash:
            raise ValueError("Raw snapshot checksum mismatch")
        if manifest["files"].get(adjusted_path.name) != adjusted_hash:
            raise ValueError("Adjusted snapshot checksum mismatch")
        raw_frame = pd.read_csv(raw_path, index_col="date", parse_dates=True)
        adjusted_frame = pd.read_csv(adjusted_path, index_col="date", parse_dates=True)
        calendar = manifest.get("calendar", "XNYS")
        validate(raw_frame, manifest["start"], manifest["end"], calendar=calendar)
        validate(adjusted_frame, manifest["start"], manifest["end"], calendar=calendar)
        if "adj_close" not in raw_frame:
            raise ValueError("Raw snapshot has no adjusted close")
        try:
            pd.testing.assert_frame_equal(
                adjust(raw_frame), adjusted_frame, check_exact=False, check_dtype=False,
                rtol=1e-10, atol=1e-10,
            )
        except AssertionError as exc:
            raise ValueError("Adjusted snapshot does not match raw prices") from exc
        if len(adjusted_frame) != manifest["rows"]:
            raise ValueError("Dataset row count differs from manifest")
        snapshot_id = self.identity(manifest, raw_hash, adjusted_hash)
        snapshot = DatasetSnapshot.model_validate({
            "id": snapshot_id, "symbol": manifest["symbol"], "source": manifest["source"],
            "market": manifest.get("market", "us_equity"),
            "venue": manifest.get("venue", "yahoo"),
            "interval": manifest.get("interval", "1d"),
            "calendar": calendar,
            "base_asset": manifest.get("base_asset"),
            "quote_asset": manifest.get("quote_asset"),
            "price_semantics": manifest.get("price_semantics", "synthetic total-return prices"),
            "exchange_rules": manifest.get("exchange_rules", {}),
            "start": manifest["start"], "end": manifest["end"],
            "retrieved_at_utc": manifest["retrieved_at_utc"], "rows": manifest["rows"],
            "adjustment": manifest["adjustment"], "raw_path": relative_raw,
            "adjusted_path": relative_adjusted, "manifest_path": relative_manifest,
            "raw_sha256": raw_hash, "adjusted_sha256": adjusted_hash,
            "manifest_sha256": manifest_hash, "status": "ready",
        })
        values = (
            snapshot.id, snapshot.symbol, snapshot.source, str(snapshot.start), str(snapshot.end),
            snapshot.retrieved_at_utc, snapshot.rows, snapshot.adjustment, snapshot.raw_path,
            snapshot.adjusted_path, snapshot.manifest_path, snapshot.raw_sha256,
            snapshot.adjusted_sha256, snapshot.manifest_sha256, snapshot.status,
            snapshot.market, snapshot.venue, snapshot.interval, snapshot.calendar,
            snapshot.base_asset, snapshot.quote_asset, snapshot.price_semantics,
            json.dumps(snapshot.exchange_rules, separators=(",", ":")),
        )
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO dataset_snapshots "
                "(id,symbol,source,start_date,end_date,retrieved_at_utc,rows,adjustment,raw_path,"
                "adjusted_path,manifest_path,raw_sha256,adjusted_sha256,manifest_sha256,status,"
                "market,venue,interval,calendar,base_asset,quote_asset,price_semantics,exchange_rules_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                values,
            )
        return self.get(snapshot_id)

    def get(self, snapshot_id):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM dataset_snapshots WHERE id = ?", (snapshot_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown dataset snapshot: {snapshot_id}")
        return DatasetSnapshot.model_validate({
            "id": row["id"], "symbol": row["symbol"], "source": row["source"],
            "market": row["market"], "venue": row["venue"], "interval": row["interval"],
            "calendar": row["calendar"], "base_asset": row["base_asset"],
            "quote_asset": row["quote_asset"], "price_semantics": row["price_semantics"],
            "exchange_rules": json.loads(row["exchange_rules_json"]),
            "start": row["start_date"], "end": row["end_date"],
            "retrieved_at_utc": row["retrieved_at_utc"], "rows": row["rows"],
            "adjustment": row["adjustment"], "raw_path": row["raw_path"],
            "adjusted_path": row["adjusted_path"], "manifest_path": row["manifest_path"],
            "raw_sha256": row["raw_sha256"], "adjusted_sha256": row["adjusted_sha256"],
            "manifest_sha256": row["manifest_sha256"], "status": row["status"],
        })

    def list_ready(self, symbol=None):
        query = "SELECT id FROM dataset_snapshots"
        params = ()
        if symbol:
            query += " WHERE symbol = ?"
            params = (symbol.upper(),)
        query += " ORDER BY retrieved_at_utc DESC, id"
        with self._connect() as connection:
            ids = [row[0] for row in connection.execute(query, params)]
        return [self.get(snapshot_id) for snapshot_id in ids]

    def load(self, snapshot_id):
        snapshot = self.get(snapshot_id)
        raw = self._resolve(snapshot.raw_path)
        adjusted = self._resolve(snapshot.adjusted_path)
        manifest_path = self._resolve(snapshot.manifest_path)
        if (digest(raw) != snapshot.raw_sha256 or digest(adjusted) != snapshot.adjusted_sha256
                or digest(manifest_path) != snapshot.manifest_sha256):
            raise ValueError("Dataset changed after registration")
        frame = pd.read_csv(adjusted, index_col="date", parse_dates=True)
        validate(frame, str(snapshot.start), str(snapshot.end), calendar=snapshot.calendar)
        if len(frame) != snapshot.rows:
            raise ValueError("Registered dataset row count changed")
        manifest = json.loads(manifest_path.read_text())
        if (manifest["symbol"] != snapshot.symbol or manifest["start"] != str(snapshot.start)
                or manifest["end"] != str(snapshot.end) or manifest["rows"] != snapshot.rows):
            raise ValueError("Dataset manifest differs from catalog")
        return frame, manifest

    def artifact_bytes(self, snapshot_id):
        """Return checksum-verified immutable price files for exact provenance comparisons."""
        snapshot = self.get(snapshot_id)
        self.load(snapshot_id)
        return {
            "raw.csv": self._resolve(snapshot.raw_path).read_bytes(),
            "adjusted.csv": self._resolve(snapshot.adjusted_path).read_bytes(),
        }


def register_legacy_pilot(catalog=None):
    catalog = catalog or DatasetCatalog()
    return catalog.register(
        catalog.base_dir / "manifest.json",
        catalog.base_dir / "spy-raw.csv",
        catalog.base_dir / "spy-adjusted.csv",
    )
