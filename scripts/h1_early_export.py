"""Read-only export of the Setup-2 H1 early entry research input (docs/setup2-h1-early-entry-research.md).

Runs inside the admin container on the VPS and writes JSON to stdout:

    docker compose ... --profile admin run --rm --no-deps -T --entrypoint /app/.venv/bin/python admin - \
        < scripts/h1_early_export.py | gzip > h1-early-input.json.gz

Per route: the coin's anchor, warm-up and Spot proxy boundary; every H4 bar of the series the slot profile
evaluates and every M15 bar from the profile start, both with volume, as Binance kline rows; and the live
observation flags of each warmed-up H4 bar, so the study can check that its local recomputation matches.
"""

import json
import os
import sys
from datetime import datetime, timezone

from intraday import setup2, setup2_store
from intraday.store import IntradayStore

COINS = ("ETHUSDT", "HYPEUSDT", "NEARUSDT", "SOLUSDT", "ZECUSDT")

os.nice(10)
store = IntradayStore("/app/state/intraday/intraday.sqlite3", read_only=True)
until = setup2.floor_h4(datetime.now(timezone.utc))
iso = lambda v: None if v is None else v.isoformat()
ms = lambda at: int(at.timestamp()*1000)
kline = lambda b: [ms(b.opened_at), str(b.open), str(b.high), str(b.low), str(b.close), str(b.volume),
                   ms(b.available_at)-1]
routes = {}
for symbol in COINS:
    settings = setup2_store.coin_settings(store, symbol)
    for market, side in (("perp", -1), ("spot", 1)):
        h4 = setup2_store.proxied_bars(store, symbol, market, "4h", since=settings.anchor, until=until,
                                       settings=settings)
        m15 = setup2_store.list_bars(store, symbol, market, "15m",
                                     since=setup2_store.profile_start(settings.anchor, settings.warmup_bars),
                                     until=until)
        _, obs = setup2.series_observations(
            h4, m15, side=side, anchor=settings.anchor, profile="setup2_slots_v1", warmup_bars=settings.warmup_bars,
            own_from=settings.proxy_until if market == "spot" else None)
        routes[f"{symbol}:{market}"] = dict(
            anchor=iso(settings.anchor), warmup_bars=settings.warmup_bars, proxy_until=iso(settings.proxy_until),
            h4=[kline(b) for b in h4], m15=[kline(b) for b in m15],
            obs=[None if o is None else [o.donchian_entry, list(o.failed_filters), list(o.blockers), o.exit,
                                         str(o.size_multiplier)] for o in obs])
        print(symbol, market, len(h4), len(m15), file=sys.stderr, flush=True)
json.dump(dict(exported_at=datetime.now(timezone.utc).isoformat(), until=until.isoformat(), routes=routes),
          sys.stdout)
