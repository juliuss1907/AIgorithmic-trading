"""Fetch one redacted snapshot for the 09:05 Vietnam daily digest."""

from __future__ import annotations

import json

from _aigt_http import OperatorReadClient


def main() -> int:
    try:
        snapshot = OperatorReadClient().snapshot()
    except (RuntimeError, ValueError) as error:
        print(json.dumps({"status": "unavailable", "error": str(error)}))
        return 0
    print(json.dumps(snapshot, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
