"""Read model of the Setup-2 soak page (`/setup2`): worker health, soak coverage per route, the latest bar of each
route, every entry signal with Jev's view, its hour score and the shadow limit entry, and Telegram delivery.

Read-only. Signal outcomes reuse the weekly learning review's simulations, so the page and the review agree."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from intraday import active_set, setup2, setup2_store
from intraday import learning_review as lr
from intraday.decision_evaluation import _bucket

SCOPE_MARKETS = lr.SCOPE_MARKETS
JEV_ERRORS = lr.JEV_ERRORS
SOAK_DAYS = 14                               # earliest gate evaluation after a campaign starts (readiness)
COVERAGE_MIN = 0.95                          # the soak gate's heartbeat threshold
WORKER_STALE = timedelta(minutes=5)          # Perp jobs run every 30 seconds
JEV_MATCH = timedelta(hours=1)               # a Jev answer belongs to the signal bar it follows
SIDE_BUCKET = {1: "long", -1: "short"}


def _rows(store, sql, args=()):
    with store._connect() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def _table(store, name):
    with store._connect() as c:
        return lr._table(c, name)


def _campaigns(store, now):
    """Active soak campaigns of the coins in the current active set (all of them when no set is recorded)."""
    if not _table(store, "replay_gate_campaigns"):
        return []
    version = active_set.current(store)
    coins = set(version.plan.coins) if version else None
    result = []
    for row in _rows(store, "SELECT symbol, scope, candidate_id, started_at FROM replay_gate_campaigns "
                            "WHERE status='active' ORDER BY symbol, scope"):
        market = SCOPE_MARKETS.get(row["scope"])
        if market is None or (coins is not None and row["symbol"] not in coins):
            continue
        started = datetime.fromisoformat(row["started_at"])
        seen = setup2_store.observations(store, row["symbol"], market, rule_id=row["candidate_id"], since=started,
                                         until=now)
        coverage = setup2_store.heartbeat(store, row["symbol"], market, row["candidate_id"], start=started, end=now)
        review_at = started+timedelta(days=SOAK_DAYS)
        result.append(dict(
            route=f"{row['symbol']}:{market}", symbol=row["symbol"], market=market, started_at=row["started_at"],
            coverage_pct=coverage*100, healthy=coverage >= COVERAGE_MIN, bars=len({r["bar_close"] for r in seen}),
            jev_errors=sum(r["jev_status"] in JEV_ERRORS for r in seen),
            last_bar=max((r["bar_close"] for r in seen), default=None), review_at=review_at.isoformat(),
            progress_pct=min(100.0, (now-started)/(review_at-started)*100)))
    return result


def _health(store, now):
    if not _table(store, "scheduler_runs"):
        return dict(last_run=None, age_seconds=None, stale=True, errors=[])
    last = _rows(store, "SELECT MAX(started_at) AS at FROM scheduler_runs WHERE status IN ('success','running')")[0]["at"]
    errors = _rows(store, "SELECT job_name, error_code, COUNT(*) AS n, MAX(scheduled_for) AS last FROM scheduler_runs "
                          "WHERE status='error' AND scheduled_for>=? GROUP BY job_name, error_code "
                          "ORDER BY last DESC", ((now-timedelta(hours=24)).isoformat(),))
    age = (now-datetime.fromisoformat(last)).total_seconds() if last else None
    return dict(last_run=last, age_seconds=age, stale=age is None or age > WORKER_STALE.total_seconds(),
                errors=errors)


def _latest_bars(store, routes):
    result = []
    for symbol, market in routes:
        seen = lr._observations(store, symbol, market)
        if not seen:
            continue
        o = max(seen, key=lambda x: x["bar_close"])
        result.append(dict(route=f"{symbol}:{market}", bar_close=o["bar_close"], close=o.get("close"),
                           entry=bool(o.get("entry")), breakout=bool(o.get("donchian_entry")), exit=bool(o.get("exit")),
                           failed_filters=o.get("failed_filters", []), blockers=o.get("blockers", []),
                           jev_status=o.get("jev_status")))
    return result


def _jev_view(store, symbol, market, bar_close, side):
    """Jev's first answer after the signal bar closed, and whether it sides with the rule."""
    if not _table(store, "signals"):
        return None
    at = datetime.fromisoformat(bar_close)
    scope = next(s for s, m in SCOPE_MARKETS.items() if m == market)
    rows = _rows(store, "SELECT jev_answers FROM signals WHERE symbol=? AND scope=? AND timestamp>=? AND timestamp<? "
                        "AND jev_answers IS NOT NULL ORDER BY timestamp LIMIT 1",
                 (symbol, scope, at.isoformat(), (at+JEV_MATCH).isoformat()))
    if not rows:
        return None
    direction = json.loads(rows[0]["jev_answers"]).get("direction") or {}
    bucket = _bucket(direction.get("choice", ""))
    stance = "agree" if bucket == SIDE_BUCKET[side] else "neutral" if bucket == "hold" else "oppose"
    return dict(choice=direction.get("choice"), confidence=direction.get("confidence"), stance=stance)


def _signals(store, routes, since, now, limit_entries):
    scores = lr._hour_scores(store, routes, [], since, now)
    limits = {i["bar_close"]+i["symbol"]: i for i in limit_entries["items"]}
    result = []
    for item in scores["items"]:
        symbol, market, bar_close = item["symbol"], item["market"], item["bar_close"]
        o = next(x for x in lr._observations(store, symbol, market, since=datetime.fromisoformat(bar_close),
                                              until=datetime.fromisoformat(bar_close)) if x["bar_close"] == bar_close)
        limit = limits.get(bar_close+symbol) if market == "perp" else None
        result.append(dict(
            route=f"{symbol}:{market}", bar_close=bar_close, side=item["side"], close=o.get("close"),
            jev_status=o.get("jev_status"), jev=_jev_view(store, symbol, market, bar_close, item["side"]),
            hour_score_pct=item["score_pct"], with_hours=item["with_hours"], simulated=item["simulated"],
            limit=None if limit is None else dict(status=limit["status"], fill=limit["fill"],
                                                  outcome=limit["limit"], diff_pct=limit.get("diff_pct"))))
    return sorted(result, key=lambda s: s["bar_close"], reverse=True), scores["summary"]["hourscore:summary"]


def _notifications(store):
    if not _table(store, "notification_outbox"):
        return dict(pending=0, failing=0, recent=[])
    counts = _rows(store, "SELECT COUNT(*) AS pending, COALESCE(SUM(last_error IS NOT NULL), 0) AS failing "
                          "FROM notification_outbox WHERE status='pending'")[0]
    recent = _rows(store, "SELECT kind, status, created_at, delivered_at, message FROM notification_outbox "
                          "ORDER BY id DESC LIMIT 8")
    for r in recent:
        r["title"] = (r.pop("message") or "").splitlines()[0][:140]
    return dict(counts, recent=recent)


def _jev_statuses(store, since):
    if not _table(store, "setup2_observations"):
        return {}
    return {r["jev_status"]: r["n"] for r in _rows(
        store, "SELECT jev_status, COUNT(*) AS n FROM setup2_observations WHERE bar_close>=? GROUP BY jev_status",
        (since.isoformat(),))}


def build_setup2_dashboard(store, *, now: datetime) -> dict:
    campaigns = _campaigns(store, now)
    since = min((datetime.fromisoformat(c["started_at"]) for c in campaigns), default=now-timedelta(days=14))
    routes = [(c["symbol"], c["market"]) for c in campaigns]
    health = _health(store, now)
    limit_entries = lr._limit_entries(store, routes, since, now) if routes else None
    signals, hour_summary = _signals(store, routes, since, now, limit_entries) if routes else ([], None)
    limits = limit_entries["summary"]["limitentry:summary"] if routes else None
    notifications = _notifications(store)
    attention = []
    if health["stale"]:
        attention.append("Worker không chạy job nào trong 5 phút gần nhất")
    attention += [f"{c['route']} heartbeat {c['coverage_pct']:.1f}%" for c in campaigns if not c["healthy"]]
    if notifications["failing"]:
        attention.append(f"{notifications['failing']} tin Telegram đang gửi lỗi")
    return dict(
        generated_at=now.isoformat(), since=since.isoformat(), health=health, campaigns=campaigns,
        latest_bars=_latest_bars(store, routes), signals=signals, hour_score=hour_summary, limit_entry=limits,
        notifications=notifications, jev_statuses=_jev_statuses(store, since),
        model_cost_24h_usd=store.model_cost_since(now-timedelta(hours=24)),
        next_bar_close=(setup2.floor_h4(now)+setup2.H4).isoformat(),
        attention=attention, status="attention" if attention else "ok")
