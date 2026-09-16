import json
import sys

from lab.__main__ import main


def test_promote_paper_check_prints_verified_contract_without_starting_gateway(
    monkeypatch, capsys, tmp_path,
):
    calls = {}

    def activate(promotion, runs, catalog, paper=None, gateway=None, *, check=False):
        calls["arguments"] = (promotion, runs, catalog, paper, gateway, check)
        return {"status": "verified", "contract_sha256": "a" * 64}

    monkeypatch.setattr("lab.paper_promotion.activate_promoted_account", activate)
    monkeypatch.setattr("lab.evaluation.PromotionStore", lambda path: ("promotion", path))
    monkeypatch.setattr("lab.store.RunStore", lambda database, runs: ("runs", database, runs))
    monkeypatch.setattr("lab.datasets.DatasetCatalog", lambda data: ("catalog", data))
    monkeypatch.setattr(sys, "argv", [
        "lab", "promote-paper", "--check",
        "--state", str(tmp_path / "promotion.sqlite3"),
        "--database", str(tmp_path / "lab.sqlite3"),
        "--runs-dir", str(tmp_path / "runs"),
        "--data-dir", str(tmp_path / "data"),
    ])

    main()

    assert json.loads(capsys.readouterr().out)["status"] == "verified"
    assert calls["arguments"][3:5] == (None, None)
    assert calls["arguments"][5] is True


def test_paper_review_can_read_status_or_finalize(monkeypatch, capsys, tmp_path):
    class Paper:
        def __init__(self, _database):
            pass

        def campaign_status(self, account_id):
            return {"account_id": account_id, "status": "running"}

        def finalize_campaign(self, account_id):
            return {"account_id": account_id, "status": "eligible", "review_sha256": "b" * 64}

    monkeypatch.setattr("lab.paper.PaperTradingService", Paper)
    monkeypatch.setattr(sys, "argv", [
        "lab", "paper-review", "--account", "paper-1",
        "--database", str(tmp_path / "lab.sqlite3"),
    ])
    main()
    assert json.loads(capsys.readouterr().out)["status"] == "running"

    monkeypatch.setattr(sys, "argv", [
        "lab", "paper-review", "--account", "paper-1", "--finalize",
        "--database", str(tmp_path / "lab.sqlite3"),
    ])
    main()
    assert json.loads(capsys.readouterr().out)["status"] == "eligible"
