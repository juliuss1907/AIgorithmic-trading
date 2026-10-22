"""Read-only export of the Setup-2 support/resistance zone research input (docs/setup2-sr-zone-research.md).

Runs inside the admin container on the VPS and writes JSON to stdout:

    docker compose ... --profile admin run --rm --no-deps -T --entrypoint /app/.venv/bin/python admin - \
        < scripts/sr_zone_export.py > sr-zone-input.json

The `series` are those of scripts/fast_filter_export.py: every H4 bar of the series the slot profile evaluates,
with the live observation from `setup2.series_observations`, the ADX of three bars back and EMA20/EMA50. The M15
windows cover both markets: for every Donchian breakout that passes every filter with no blocker, the route's
M15 bars of the 13 H4 bars after it, keyed `symbol:market:opened_at`, for the limit entries.
"""

import json
import os
import sys
from datetime import datetime, timezone

from intraday import setup2, setup2_store
from intraday.replay_v2.donchian_filters import indicator_series
from intraday.store import IntradayStore

COINS = ("ETHUSDT", "HYPEUSDT", "NEARUSDT", "SOLUSDT", "ZECUSDT")
FEATURES = ("atr", "adx", "prior_adx", "plus_di", "minus_di", "volume", "volume_ma")
WINDOW_QUARTERS = 13*16

os.nice(10)
store = IntradayStore("/app/state/intraday/intraday.sqlite3", read_only=True)
until = setup2.floor_h4(datetime.now(timezone.utc))
text = lambda v: None if v is None else str(v)
routes, m15_windows = {}, {}
for symbol in COINS:
    settings = setup2_store.coin_settings(store, symbol)
    for market, side in (("perp", -1), ("spot", 1)):
        h4 = setup2_store.proxied_bars(store, symbol, market, "4h", since=settings.anchor, until=until,
                                       settings=settings)
        m15 = setup2_store.list_bars(store, symbol, market, "15m",
                                     since=setup2_store.profile_start(settings.anchor, settings.warmup_bars),
                                     until=until)
        series, obs = setup2.series_observations(
            h4, m15, side=side, anchor=settings.anchor, profile="setup2_slots_v1", warmup_bars=settings.warmup_bars,
            own_from=settings.proxy_until if market == "spot" else None)
        series = series or []
        fast = indicator_series(series, ema_fast=20, ema_slow=50)
        rows = []
        for i, (bar, o) in enumerate(zip(series, obs)):
            item = None
            if o is not None:
                f = o.features
                item = dict(donchian=o.donchian_entry, failed=list(o.failed_filters), blockers=list(o.blockers),
                            exit=o.exit, mult=str(o.size_multiplier), **{k: text(f.get(k)) for k in FEATURES},
                            adx3=text(obs[i-3].features.get("adx")) if i >= 3 and obs[i-3] is not None else None,
                            ema20=text(fast[i]["ema_fast"]), prior_ema20=text(fast[i]["prior_ema_fast"]),
                            ema50=text(fast[i]["ema_slow"]))
            rows.append([bar.opened_at.isoformat(), str(bar.open), str(bar.high), str(bar.low), str(bar.close), item])
        routes[f"{symbol}:{market}"] = rows
        by_open = {b.opened_at: b for b in m15}
        for bar, o in zip(series, obs):
            if o is not None and o.entry:
                window = [by_open.get(bar.available_at+k*setup2.M15) for k in range(WINDOW_QUARTERS)]
                m15_windows[f"{symbol}:{market}:{bar.opened_at.isoformat()}"] = [
                    None if b is None else [b.opened_at.isoformat(), str(b.open), str(b.high), str(b.low),
                                            str(b.close)] for b in window]
        print(symbol, market, len(rows), file=sys.stderr, flush=True)
json.dump(dict(exported_at=datetime.now(timezone.utc).isoformat(), until=until.isoformat(), series=routes,
               m15=m15_windows), sys.stdout)
