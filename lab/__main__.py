import argparse
import json
from pathlib import Path

from lab.data import fetch
from lab.experiment import compare, read_config, run
from lab.report import generate


def main():
    parser = argparse.ArgumentParser(description="SPY research lab — fixed rules, saved evidence")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("fetch", help="Download one immutable Yahoo snapshot")
    runner = commands.add_parser("run", help="Run all cases from the local snapshot")
    runner.add_argument("--output", type=Path, required=True, help="New output directory")
    comparer = commands.add_parser("compare", help="Prove two runs are numerically identical")
    comparer.add_argument("first", type=Path)
    comparer.add_argument("second", type=Path)
    args = parser.parse_args()
    if args.command == "fetch":
        print(json.dumps(fetch(read_config()), indent=2))
    elif args.command == "run":
        results = run(read_config(), args.output)
        generate(args.output)
        print(f"Completed {len(results)} cases. Report: {args.output / 'report.md'}")
    else:
        print(json.dumps(compare(args.first, args.second), indent=2))


if __name__ == "__main__":
    main()
