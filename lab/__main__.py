import argparse
import json
from pathlib import Path

from lab.data import fetch
from lab.experiment import compare, compare_results, read_config, run
from lab.report import generate


def main():
    parser = argparse.ArgumentParser(description="System trading lab — fixed rules, saved evidence")
    commands = parser.add_subparsers(dest="command", required=True)
    fetcher = commands.add_parser("fetch", help="Download one immutable market snapshot")
    fetcher.add_argument("--config", type=Path, help="Experiment JSON")
    runner = commands.add_parser("run", help="Run all cases from the local snapshot")
    runner.add_argument("--config", type=Path, help="Experiment JSON (defaults to experiment.json)")
    runner.add_argument("--output", type=Path, required=True, help="New output directory")
    comparer = commands.add_parser("compare", help="Prove two runs are numerically identical")
    comparer.add_argument("first", type=Path)
    comparer.add_argument("second", type=Path)
    regression = commands.add_parser("compare-results", help="Compare numeric results across refactors")
    regression.add_argument("first", type=Path)
    regression.add_argument("second", type=Path)
    gate = commands.add_parser("gate", help="Freeze a BTC promotion decision before holdout")
    gate.add_argument("--candidate", action="append", required=True, metavar="NAME=RUN_DIR")
    gate.add_argument("--state", type=Path, default=Path("state/btc-promotion.sqlite3"))
    holdout = commands.add_parser("holdout", help="Open the selected BTC holdout exactly once")
    holdout.add_argument("--candidate", required=True)
    holdout.add_argument("--run", type=Path, required=True)
    holdout.add_argument("--state", type=Path, default=Path("state/btc-promotion.sqlite3"))
    args = parser.parse_args()
    if args.command == "fetch":
        print(json.dumps(fetch(read_config(args.config)).model_dump(mode="json"), indent=2))
    elif args.command == "run":
        results = run(read_config(args.config), args.output)
        generate(args.output)
        print(f"Completed {len(results)} cases. Report: {args.output / 'report.md'}")
    elif args.command == "compare":
        print(json.dumps(compare(args.first, args.second), indent=2))
    elif args.command == "compare-results":
        print(json.dumps(compare_results(args.first, args.second), indent=2))
    elif args.command == "gate":
        from lab.evaluation import PromotionStore, select_candidate

        candidates = {}
        for value in args.candidate:
            if "=" not in value:
                parser.error("--candidate must use NAME=RUN_DIR")
            name, directory = value.split("=", 1)
            candidates[name] = json.loads((Path(directory) / "summary.json").read_text())
        decision = PromotionStore(args.state).freeze(select_candidate(candidates))
        print(json.dumps(decision, indent=2))
    else:
        from lab.evaluation import PromotionStore

        summary = json.loads((args.run / "summary.json").read_text())
        metrics = summary["holdout/5bps/rule"]
        result = PromotionStore(args.state).open_holdout(args.candidate, metrics)
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
