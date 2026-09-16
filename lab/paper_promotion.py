"""Verification boundary for turning frozen research evidence into paper trading."""

import hashlib
import json

from lab.contracts import CandidateLock, HoldoutResult


def build_campaign_contract(state, exchange_rules):
    return {
        "selected": state["selected"],
        "candidate_lock": state["candidate_lock"],
        "holdout": state["holdout"],
        "execution": {
            "initial_cash": 10_000,
            "max_target_weight": .5,
            "halt_drawdown": .20,
            "fee_bps": 10,
            "slippage_bps": 5,
            "exchange_rules": exchange_rules,
            "start_policy": "wait_for_new_entry",
        },
    }


def contract_sha256(contract):
    payload = json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _artifact(run_store, run_id, relative_path, expected_sha256):
    registered = next(
        (item for item in run_store.list_artifacts(run_id)
         if item["relative_path"] == relative_path),
        None,
    )
    if registered is None:
        raise ValueError(f"Registered run is missing {relative_path}")
    path, _ = run_store.artifact(run_id, registered["id"])
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha256:
        raise ValueError(f"Promotion artifact checksum differs: {run_id}/{relative_path}")
    return path


def _verify_run(run_store, evidence):
    run = run_store.get_run(evidence["run_id"])
    if run["dataset_id"] != evidence["dataset_id"]:
        raise ValueError("Promotion run dataset differs from frozen evidence")
    summary = _artifact(
        run_store, evidence["run_id"], "summary.json", evidence["summary_sha256"]
    )
    provenance = _artifact(
        run_store, evidence["run_id"], "provenance.json", evidence["provenance_sha256"]
    )
    return json.loads(summary.read_text()), json.loads(provenance.read_text())


def _verify_dataset(catalog, snapshot_id):
    snapshot = catalog.get(snapshot_id)
    catalog.load(snapshot_id)
    identity = (
        snapshot.market, snapshot.venue, snapshot.symbol, snapshot.interval, snapshot.calendar
    )
    if identity != ("crypto_spot", "binance", "BTCUSDT", "1d", "UTC_24_7"):
        raise ValueError("Promotion dataset is not the locked BTCUSDT daily Binance contract")
    return snapshot


def verify_promoted_contract(promotion_store, run_store, catalog):
    """Verify every mutable boundary before allowing a promoted account to be created."""
    state = promotion_store.get()
    if state.get("status") != "candidate_selected" or state.get("selected") is None:
        raise ValueError("Promotion gate did not select a candidate")
    lock = CandidateLock.model_validate(state.get("candidate_lock"))
    holdout = HoldoutResult.model_validate(state.get("holdout"))
    if lock.candidate != state["selected"] or holdout.candidate != lock.candidate:
        raise ValueError("Promotion candidate lineage differs across gate, lock, and holdout")
    if not holdout.passed:
        raise ValueError("Promotion requires a passing holdout")

    _, candidate_provenance = _verify_run(run_store, lock.model_dump(mode="json"))
    _, holdout_provenance = _verify_run(run_store, holdout.model_dump(mode="json"))
    candidate_experiment = candidate_provenance.get("experiment", {})
    holdout_experiment = holdout_provenance.get("experiment", {})
    locked_strategy = lock.strategy.model_dump(mode="json")
    locked_sizing = lock.position_sizing.model_dump(mode="json")
    for experiment in (candidate_experiment, holdout_experiment):
        if experiment.get("strategy") != locked_strategy:
            raise ValueError("Run strategy differs from the locked candidate")
        if experiment.get("risk_policy", {}).get("position_sizing") != locked_sizing:
            raise ValueError("Run sizing differs from the locked candidate")
    if candidate_experiment.get("dataset_id") != lock.dataset_id:
        raise ValueError("Candidate provenance dataset differs from the lock")
    if holdout_experiment.get("dataset_id") != holdout.dataset_id:
        raise ValueError("Holdout provenance dataset differs from the result")

    _verify_dataset(catalog, lock.dataset_id)
    holdout_snapshot = _verify_dataset(catalog, holdout.dataset_id)
    rules = holdout_snapshot.exchange_rules
    if set(rules) != {"quantity_step", "min_notional"}:
        raise ValueError("Holdout snapshot has incomplete exchange rules")
    if float(rules["quantity_step"]) <= 0 or float(rules["min_notional"]) <= 0:
        raise ValueError("Holdout snapshot has invalid exchange rules")

    contract = build_campaign_contract(state, rules)
    return {
        "candidate_run_id": lock.run_id,
        "holdout_run_id": holdout.run_id,
        "holdout_dataset_id": holdout.dataset_id,
        "exchange_rules": rules,
        "contract_sha256": contract_sha256(contract),
        "state": state,
    }


def activate_promoted_account(
    promotion_store, run_store, catalog, paper_service=None, gateway=None, *, check=False,
):
    verified = verify_promoted_contract(promotion_store, run_store, catalog)
    public_result = {key: value for key, value in verified.items() if key != "state"}
    if check:
        return {"status": "verified", **public_result}
    if paper_service is None or gateway is None:
        raise ValueError("Paper service and public market gateway are required for activation")
    account = paper_service.create_promoted_account(
        promotion_store, exchange_rules=verified["exchange_rules"]
    )
    campaign = paper_service.get_campaign(account["id"])
    bootstrap = next(
        (cycle for cycle in paper_service.list_cycles(account["id"])
         if cycle["entry_point"] == "bootstrap"),
        None,
    )
    if campaign["started_at"] is None:
        market = gateway.snapshot()
        bootstrap = paper_service.run_cycle(
            account["id"], market["frame"], bid=market["bid"], ask=market["ask"],
            exchange_rules=market["exchange_rules"],
            dataset_snapshot_id=market["dataset_snapshot_id"], entry_point="bootstrap",
        )
    return {
        "status": "active", **public_result, "account": paper_service.get_account(account["id"]),
        "campaign": paper_service.campaign_status(account["id"]), "bootstrap": bootstrap,
    }
