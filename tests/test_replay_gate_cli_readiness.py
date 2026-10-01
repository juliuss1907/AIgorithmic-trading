from datetime import timedelta
import json
import sys

import pytest

from intraday.__main__ import main
from intraday.asset_readiness import build_asset_readiness, format_asset_readiness
from intraday.replay_v2.lifecycle import replay_gate, start_gate_soak
from test_replay_gate_lifecycle import setup, NOW


def test_cli_replay_v2_persists_only_v2_evaluation_and_reports(tmp_path, monkeypatch, capsys):
    store, rule, root = setup(tmp_path)
    monkeypatch.setattr(sys,"argv",["aigt","assets","rules","replay",rule.rule_id,"--engine","v2",
        "--database",str(store.database),"--report-dir",str(root)])
    main()
    result = json.loads(capsys.readouterr().out)
    assert result["engine_version"] == "gate-v2.1"
    assert result["replay_config"]["profile"]["spot_fee_bps"] == "10"
    assert store.latest_scoped_rule_evaluation(rule.rule_id,kind="replay") is None


def test_readiness_displays_v2_pass_costs_and_fresh_campaign_without_writes(tmp_path):
    store, rule, root = setup(tmp_path)
    saved = replay_gate(store,rule.rule_id,now=NOW,report_dir=root)
    before = store.database.read_bytes()
    report = build_asset_readiness(store.database,symbol="ETH",market="spot",now=NOW)
    row = report["rows"][0]
    assert row["gate_version"] == "gate-v2.1"
    assert row["phase"] == "awaiting_spot_soak"
    assert row["replay"]["evaluation_id"] == saved.evaluation_id
    assert row["cost_profile"]["spot_fee_bps"] == "10"
    assert "gate-v2.1" in format_asset_readiness(report)
    assert store.database.read_bytes() == before
    campaign = start_gate_soak(store,rule.rule_id,evaluation_id=saved.evaluation_id,now=NOW,report_dir=root)
    before = store.database.read_bytes()
    row = build_asset_readiness(store.database,symbol="ETH",market="spot",now=NOW+timedelta(days=1))["rows"][0]
    assert row["phase"] == "spot_soak"
    assert row["earliest_evaluation_at"] == (NOW+timedelta(days=14)).isoformat()
    assert row["started_at"] == campaign["started_at"]
    assert store.database.read_bytes() == before


def test_v1_activation_entrypoint_cannot_bypass_selected_v2_validation(tmp_path):
    from intraday.spot_4h_lifecycle import evaluate_spot_4h_soak
    store, rule, root = setup(tmp_path)
    saved = replay_gate(store,rule.rule_id,now=NOW,report_dir=root)
    start_gate_soak(store,rule.rule_id,evaluation_id=saved.evaluation_id,now=NOW,report_dir=root)
    with pytest.raises(ValueError,match="no v1 fallback"):
        evaluate_spot_4h_soak(store,rule.rule_id,now=NOW+timedelta(days=20))
