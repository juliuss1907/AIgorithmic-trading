"""Read model of the Setup-2 soak page (`/setup2`): worker health, soak coverage per route, the latest bar of each
route, every entry signal with Jev's view, its hour score and the shadow limit entry, and Telegram delivery.

Read-only. Signal outcomes reuse the weekly learning review's simulations, so the page and the review agree."""

from __future__ import annotations

import json
import math
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
    adx_rise = lr._adx_rise_entries(store, routes, since, now) if routes else None
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
        adx_rise=None if adx_rise is None else dict(summary=adx_rise["summary"]["adxrise:summary"], items=sorted(
            adx_rise["items"], key=lambda i: i["bar_close"], reverse=True)),
        notifications=notifications, jev_statuses=_jev_statuses(store, since),
        model_cost_24h_usd=store.model_cost_since(now-timedelta(hours=24)),
        next_bar_close=(setup2.floor_h4(now)+setup2.H4).isoformat(),
        attention=attention, status="attention" if attention else "ok")


# --- Price chart ---------------------------------------------------------------------------------------------

CHART_BARS = 120                             # H4 bars shown: 20 days
CHART_W, PRICE_H, VOLUME_H, GAP, AXIS_W, AXIS_H = 1000, 340, 60, 12, 84, 26
CHANNEL = setup2.ENTRY_WINDOWS["setup2_slots_v1"]
STOP_BARS = 6                                # stop line length when the simulated exit is not in view


def _ticks(low, high, count=5):
    """Round price levels between low and high for the axis."""
    span = high-low
    if span <= 0:
        return [low]
    raw = span/count
    magnitude = 10**math.floor(math.log10(raw))
    step = next(m*magnitude for m in (1, 2, 2.5, 5, 10) if m*magnitude >= raw)
    first = math.ceil(low/step)*step
    return [first+k*step for k in range(int((high-first)//step)+1)]


def _price(value):
    return f"{value:.6g}"


def price_chart(store, route, signals=(), *, now, bars=CHART_BARS):
    """SVG geometry of one route's last `bars` H4 candles with EMA50, EMA200, the 25-bar Donchian entry channel,
    volume and the rule's entry signals (initial stop and shadow limit level); no candles without history."""
    from intraday.replay_v2.donchian_filters import indicator_series
    symbol, market = route.split(":")
    stored = setup2_store.list_bars(store, symbol, market, "4h")
    if not stored:
        return dict(route=route, candles=[])
    settings = setup2_store.coin_settings(store, symbol, anchor=stored[0].opened_at)
    series = setup2.anchored(setup2_store.proxied_bars(store, symbol, market, "4h", since=settings.anchor,
                                                       until=setup2.floor_h4(now), settings=settings),
                             settings.anchor) or []
    if len(series) <= CHANNEL:
        return dict(route=route, candles=[])
    indicators = indicator_series(series)
    first = max(CHANNEL, len(series)-bars)
    window = series[first:]
    upper = [float(max(b.high for b in series[i-CHANNEL:i])) for i in range(first, len(series))]
    lower = [float(min(b.low for b in series[i-CHANNEL:i])) for i in range(first, len(series))]
    ema = {k: [None if indicators[i][k] is None else float(indicators[i][k]) for i in range(first, len(series))]
           for k in ("ema50", "ema200")}
    index = {b.available_at.isoformat(): k for k, b in enumerate(window)}
    simulated = {s["bar_close"]: s for s in signals if s["route"] == route}
    marks = []
    for o in lr._observations(store, symbol, market, since=window[0].available_at):
        k = index.get(o["bar_close"])
        if k is None or not o.get("entry") or not o.get("atr"):
            continue
        profile = lr.PROFILES_BY_VERSION.get(o.get("evaluator_version"), "setup2_v1")
        stop = float(setup2.initial_stop(o["side"], o["close"], o["atr"], cap=setup2.stop_cap(profile)))
        seen = simulated.get(o["bar_close"], {})
        exit_at = (seen.get("simulated") or {}).get("exit_at")
        end = index.get(exit_at, min(len(window)-1, k+STOP_BARS)) if exit_at else min(len(window)-1, k+STOP_BARS)
        level = float(o["close"])+float(lr.LIMIT_ATR)*float(o["atr"]) if o["side"] < 0 else None
        marks.append(dict(k=k, side=o["side"], close=float(o["close"]), stop=stop, end=max(end, k+1), level=level,
                          at=o["bar_close"]))
    values = [float(v) for b in window for v in (b.high, b.low)]+upper+lower
    values += [v for line in ema.values() for v in line if v is not None]+[m["stop"] for m in marks]
    low, high = min(values), max(values)
    pad = (high-low)*0.03 or high*0.01
    low, high = low-pad, high+pad
    plot_w = CHART_W-AXIS_W
    step = plot_w/len(window)
    x = lambda k: step*(k+0.5)
    y = lambda v: PRICE_H*(high-v)/(high-low)
    line = lambda vals: " ".join(f"{x(k):.1f},{y(v):.1f}" for k, v in enumerate(vals) if v is not None)
    stair = lambda vals: " ".join(f"{x(k)-step/2:.1f},{y(v):.1f} {x(k)+step/2:.1f},{y(v):.1f}" for k, v in enumerate(vals))
    top_volume = max(float(b.volume) for b in window) or 1.0
    vol_y = PRICE_H+GAP
    candles = []
    for k, b in enumerate(window):
        o, h, l, c = (float(v) for v in (b.open, b.high, b.low, b.close))
        height = max(1.0, abs(y(o)-y(c)))
        candles.append(dict(
            x=x(k), body_x=x(k)-step*0.35, body_w=step*0.7, body_y=min(y(o), y(c)), body_h=height,
            wick_top=y(h), wick_bottom=y(l), up=c >= o, vol_y=vol_y+VOLUME_H*(1-float(b.volume)/top_volume),
            vol_h=VOLUME_H*float(b.volume)/top_volume,
            title=f"{b.opened_at.isoformat()} O {_price(o)} H {_price(h)} L {_price(l)} C {_price(c)}"))
    days = sorted({k for k, b in enumerate(window) if b.opened_at.hour == 0 and b.opened_at.day % 2 == 0})
    last = float(window[-1].close)
    return dict(
        route=route, width=CHART_W, height=PRICE_H+GAP+VOLUME_H+AXIS_H, plot_w=plot_w, price_h=PRICE_H,
        candles=candles, ema50=line(ema["ema50"]), ema200=line(ema["ema200"]), upper=stair(upper), lower=stair(lower),
        y_ticks=[dict(y=y(v), label=_price(v)) for v in _ticks(low, high)],
        x_ticks=[dict(x=x(k), at=window[k].opened_at.isoformat()) for k in days],
        last=dict(y=y(last), label=_price(last), up=last >= float(window[-1].open)),
        marks=[dict(x=x(m["k"]), side=m["side"],
                    # Short: above the high pointing down; long: below the low pointing up.
                    y=(y(float(window[m["k"]].high))-4) if m["side"] < 0 else (y(float(window[m["k"]].low))+4),
                    tip_y=(y(float(window[m["k"]].high))-18) if m["side"] < 0 else (y(float(window[m["k"]].low))+18),
                    stop_y=y(m["stop"]), stop_x2=x(m["end"]), stop_label=_price(m["stop"]),
                    level_y=None if m["level"] is None else y(m["level"]), level_x2=x(min(len(window)-1, m["k"]+1)),
                    title=f"{m['at']} {'Long' if m['side'] > 0 else 'Short'} {_price(m['close'])} · stop {_price(m['stop'])}")
               for m in marks],
        first_at=window[0].opened_at.isoformat(), last_at=window[-1].opened_at.isoformat(),
        legend=dict(ema50=ema["ema50"][-1], ema200=ema["ema200"][-1], upper=upper[-1], lower=lower[-1]))
