from datetime import datetime, timezone

import pytest

from intraday.contracts import DecisionScope, FeatureSnapshot
from intraday.store import IntradayStore


NOW = datetime(2026, 9, 30, tzinfo=timezone.utc)


def test_register_new_coin_is_persistent_idempotent_and_scope_specific(tmp_path):
    store = IntradayStore(tmp_path / "source.sqlite")
    spec = store.register_asset("DOGE", market="spot", now=NOW)
    assert spec.symbol == "DOGEUSDT"
    assert spec.enabled_scopes == frozenset({DecisionScope.SPOT_4H})
    assert spec.binance_spot_symbol is None
    assert store.register_asset("dogeusdt", market="spot", now=NOW) == spec
    reopened = IntradayStore(store.database)
    assert reopened.asset_spec("DOGE").symbol == "DOGEUSDT"
    assert reopened.asset_lifecycle("DOGE", DecisionScope.SPOT_4H).stage.value == "shadow"
    with pytest.raises(ValueError, match="not registered"):
        reopened.asset_lifecycle("DOGE", DecisionScope.PERP_INTRADAY)
    reopened.register_asset("DOGE", market="perp", now=NOW)
    assert len(reopened.list_asset_lifecycles("DOGE")) == 2


def test_catalogs_do_not_leak_assets_between_databases(tmp_path):
    one = IntradayStore(tmp_path / "one.sqlite")
    two = IntradayStore(tmp_path / "two.sqlite")
    one.register_asset("DOGE", market="perp", now=NOW)
    with pytest.raises(ValueError, match="unsupported asset"):
        two.asset_spec("DOGE")


def test_dynamic_asset_snapshots_keep_checksum_and_old_evidence(tmp_path):
    store = IntradayStore(tmp_path / "source.sqlite")
    store.register_asset("DOGE", market="perp", now=NOW)
    sample = FeatureSnapshot.create(
        symbol="DOGEUSDT", event_time=NOW, built_at=NOW,
        bid=.1, ask=.101, features={"price": .1}, freshness={"book": True},
    )
    store.record_snapshot(sample)
    assert IntradayStore(store.database).latest_snapshot(symbol="DOGEUSDT") == sample
    assert store.schema_version() == 23
    other = IntradayStore(tmp_path / "unregistered.sqlite")
    with pytest.raises(ValueError, match="unsupported asset"):
        other.record_snapshot(sample)


def test_new_asset_only_creates_collectors_for_its_verified_market(tmp_path):
    import json
    from dataclasses import replace
    from intraday.assets import spec_payload
    from intraday.config import IntradayConfig
    from intraday.__main__ import _new_asset_market_caches

    store = IntradayStore(tmp_path / "source.sqlite")
    spec = store.register_asset("DOGE", market="spot", now=NOW)
    with store._connect() as connection:
        connection.execute("UPDATE asset_catalog SET payload_json=? WHERE symbol=?",
                           (json.dumps(spec_payload(replace(spec, binance_spot_symbol=spec.symbol))), spec.symbol))
    perp, spot = _new_asset_market_caches(IntradayConfig(), store=store)
    assert "DOGEUSDT" in spot and "DOGEUSDT" not in perp
