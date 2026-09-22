import json

import pandas as pd
import pytest

from lab.contracts import ExperimentSpec
from lab.data import load, save_snapshot, sessions
from lab.datasets import DatasetCatalog


def experiment(dataset_id=None):
    return ExperimentSpec.model_validate({
        "title": "Small snapshot test",
        "hypothesis": "A deterministic fixture should replay offline.",
        "symbol": "SPY",
        "dataset_id": dataset_id,
        "data": {"start": "2025-01-02", "end_exclusive": "2025-01-11"},
        "strategy": {"family": "sma_crossover", "fast_window": 1, "slow_window": 2},
        "initial_cash": 1000,
        "slippage_bps": [0],
        "commission": 0,
        "periods": {"evaluation": ["2025-01-03", "2025-01-10"]},
    })


@pytest.fixture
def raw_bars():
    index = sessions("2025-01-02", "2025-01-10")
    close = pd.Series(range(100, 100 + len(index)), index=index, dtype=float)
    return pd.DataFrame({
        "Open": close - 0.5,
        "High": close + 1,
        "Low": close - 1,
        "Close": close,
        "Adj Close": close * 0.9,
        "Volume": 1000,
    }, index=index)


def test_content_addressed_registration_is_idempotent(tmp_path, raw_bars):
    catalog = DatasetCatalog(tmp_path / "data")
    first = save_snapshot(raw_bars, experiment(), catalog, "2025-01-11T00:00:00+00:00")
    second = save_snapshot(raw_bars, experiment(), catalog, "2025-01-11T00:00:00+00:00")

    assert first.id == second.id
    assert len(first.id) == 64
    assert len(catalog.list_ready()) == 1
    assert first.raw_path == f"snapshots/{first.id}/raw.csv"


def test_incomplete_download_is_never_published(tmp_path, raw_bars):
    catalog = DatasetCatalog(tmp_path / "data")

    with pytest.raises(ValueError, match="Session mismatch"):
        save_snapshot(raw_bars.iloc[1:], experiment(), catalog)

    assert catalog.list_ready() == []


@pytest.mark.parametrize("target", ["raw_path", "adjusted_path", "manifest_path"])
def test_changed_snapshot_is_not_replayed(tmp_path, raw_bars, target):
    catalog = DatasetCatalog(tmp_path / "data")
    snapshot = save_snapshot(raw_bars, experiment(), catalog)
    path = catalog.base_dir / getattr(snapshot, target)
    path.write_bytes(path.read_bytes() + b"\n")

    with pytest.raises(ValueError, match="changed after registration"):
        catalog.load(snapshot.id)


def test_selected_snapshot_replays_without_network(tmp_path, raw_bars, monkeypatch):
    catalog = DatasetCatalog(tmp_path / "data")
    snapshot = save_snapshot(raw_bars, experiment(), catalog)
    selected = experiment(snapshot.id)

    monkeypatch.setattr("lab.data._download_yahoo", lambda _config: pytest.fail("network used"))
    frame, manifest = load(selected, catalog)

    assert len(frame) == snapshot.rows
    assert manifest["files"]["adjusted.csv"] == snapshot.adjusted_sha256


def test_manifest_with_wrong_checksum_cannot_be_registered(tmp_path, raw_bars):
    catalog = DatasetCatalog(tmp_path / "data")
    snapshot = save_snapshot(raw_bars, experiment(), catalog)
    manifest_path = catalog.base_dir / snapshot.manifest_path
    manifest = json.loads(manifest_path.read_text())
    manifest["files"]["raw.csv"] = "0" * 64
    bad_manifest = manifest_path.with_name("bad-manifest.json")
    bad_manifest.write_text(json.dumps(manifest))

    with pytest.raises(ValueError, match="Raw snapshot checksum mismatch"):
        catalog.register(
            bad_manifest,
            catalog.base_dir / snapshot.raw_path,
            catalog.base_dir / snapshot.adjusted_path,
        )
