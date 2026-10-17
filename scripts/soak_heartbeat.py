"""Read-only Setup-2 soak heartbeat of every active gate campaign, under the old and the new rule.

Runs inside the worker container (standard library only, so it works before and after a deploy):

    docker compose ... exec -T worker python - < scripts/soak_heartbeat.py

"old" leaves out bars whose Jev call failed (code before a88825b); "new" counts every bar the rule
evaluated (a88825b). The soak gate needs 95% coverage per route.
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

DATABASE = "file:/app/state/intraday/intraday.sqlite3?mode=ro"
MARKETS = {"spot_4h": "spot", "perp_intraday": "perp"}
H4 = timedelta(hours=4)
JEV_ERRORS = ("provider_error", "gate_error")


def floor_h4(at):
    return at.replace(hour=at.hour - at.hour % 4, minute=0, second=0, microsecond=0)


def main():
    now = datetime.now(timezone.utc)
    connection = sqlite3.connect(DATABASE, uri=True)
    connection.row_factory = sqlite3.Row
    campaigns = connection.execute(
        "SELECT symbol, scope, candidate_id, started_at FROM replay_gate_campaigns "
        "WHERE status='active' ORDER BY symbol, scope").fetchall()
    print(f"now {now:%Y-%m-%d %H:%M}Z, {len(campaigns)} active campaigns")
    print(f"{'route':<16}{'started':<18}{'expected':>9}{'old':>8}{'new':>8}{'jev_err':>8}  last bar")
    for campaign in campaigns:
        market = MARKETS.get(campaign["scope"], campaign["scope"])
        started = datetime.fromisoformat(campaign["started_at"])
        rows = connection.execute(
            "SELECT bar_close, jev_status FROM setup2_observations WHERE symbol=? AND market=? AND rule_id=? "
            "AND bar_close>=? AND bar_close<=?",
            (campaign["symbol"], market, campaign["candidate_id"], started.isoformat(), now.isoformat())).fetchall()
        expected = max(1, int((floor_h4(now) - floor_h4(started)) / H4))
        every = {r["bar_close"] for r in rows}
        healthy = {r["bar_close"] for r in rows if r["jev_status"] not in JEV_ERRORS}
        errors = sum(r["jev_status"] in JEV_ERRORS for r in rows)
        last = max(every) if every else "-"
        print(f"{campaign['symbol'] + ' ' + market:<16}{started:%m-%d %H:%M}Z{'':<6}{expected:>9}"
              f"{min(1, len(healthy) / expected):>8.1%}{min(1, len(every) / expected):>8.1%}{errors:>8}  {last}")
    statuses = connection.execute(
        "SELECT jev_status, COUNT(*) AS n FROM setup2_observations WHERE bar_close>=? GROUP BY jev_status",
        (min((c["started_at"] for c in campaigns), default=now.isoformat()),)).fetchall()
    print("jev_status since first campaign:", json.dumps({r["jev_status"]: r["n"] for r in statuses}))


if __name__ == "__main__":
    main()
