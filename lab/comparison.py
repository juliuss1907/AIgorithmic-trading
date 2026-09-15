"""Condition-aware comparisons of immutable experiment runs."""

import json

from lab.contracts import ExperimentSpec


COMPARISON_FIELDS = (
    "final_equity", "total_return", "cagr_252", "max_drawdown", "round_trips"
)


def load_experiment(store, run_id):
    detail = store.detail(run_id)
    provenance = next(
        item for item in detail["artifacts"] if item["relative_path"] == "provenance.json"
    )
    path, _ = store.artifact(run_id, provenance["id"])
    payload = json.loads(path.read_text())
    config = ExperimentSpec.model_validate(payload["experiment"])
    return detail, config


def validate_clone_config(store, config):
    if config.parent_run_id is None:
        return
    _, parent = load_experiment(store, config.parent_run_id)
    shared = ("symbol", "dataset_id", "data", "periods", "initial_cash", "slippage_bps", "commission")
    changed = [field for field in shared if getattr(config, field) != getattr(parent, field)]
    expected_observed = set(parent.prior_observed_periods) | set(parent.periods)
    if set(config.prior_observed_periods) != expected_observed:
        changed.append("prior_observed_periods")
    if changed:
        raise ValueError(f"Clone must preserve shared conditions: {', '.join(changed)}")


def _context(store, run_id):
    detail, config = load_experiment(store, run_id)
    return detail["run"], detail["summary"], config


def _conditions(run, config):
    payload = config.to_json_dict()
    return {
        "symbol": config.symbol,
        "dataset_id": run["dataset_id"],
        "periods": payload["periods"],
        "initial_cash": config.initial_cash,
        "slippage_bps": payload["slippage_bps"],
        "commission": config.commission,
    }


def _run_card(run):
    return {
        key: run[key]
        for key in ("id", "title", "symbol", "strategy_label", "parent_run_id")
    }


def compare_runs(store, left_id, right_id):
    if left_id == right_id:
        raise ValueError("Hãy chọn hai run khác nhau")
    left_run, left_summary, left_config = _context(store, left_id)
    right_run, right_summary, right_config = _context(store, right_id)
    left_conditions = _conditions(left_run, left_config)
    right_conditions = _conditions(right_run, right_config)
    differences = [
        {"field": field, "left": left_conditions[field], "right": right_conditions[field]}
        for field in left_conditions
        if left_conditions[field] != right_conditions[field]
    ]
    if set(left_summary) != set(right_summary):
        differences.append({
            "field": "cases", "left": sorted(left_summary), "right": sorted(right_summary)
        })
    compatible = not differences
    cases = []
    for name in sorted(set(left_summary) | set(right_summary)):
        left = left_summary.get(name)
        right = right_summary.get(name)
        delta = None
        if compatible and left is not None and right is not None:
            delta = {
                field: round(right[field] - left[field], 12) for field in COMPARISON_FIELDS
            }
        cases.append({"case": name, "left": left, "right": right, "delta": delta})
    return {
        "compatible": compatible,
        "differences": differences,
        "left": _run_card(left_run),
        "right": _run_card(right_run),
        "cases": cases,
    }
