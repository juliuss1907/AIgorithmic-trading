"""Read-only export of the Setup-3 research input (docs/setup3-ema200-retest-research.md).

Runs inside the admin container on the VPS and writes JSON to stdout:

    docker compose ... --profile admin run --rm --no-deps -T --entrypoint /app/.venv/bin/python admin - \
        < scripts/setup3_export.py > setup3-input.json

Per route (coin x market): the H4 series the Setup-2 rule evaluates (from the coin's indicator anchor, Spot
bars before the proxy boundary priced from Perp) with EMA50, EMA200 and ATR14 from the same
`indicator_series` pass, null before the coin's warm-up, and the market's own M15 bars for intrabar order.
"""

import json
import os
import sys
from datetime import datetime, timezone

from intraday import setup2, setup2_store
from intraday.replay_v2.donchian_filters import indicator_series
from intraday.store import IntradayStore

COINS = ("ETHUSDT", "HYPEUSDT", "NEARUSDT", "SOLUSDT", "ZECUSDT")

os.nice(10)
store = IntradayStore("/app/state/intraday/intraday.sqlite3", read_only=True)
until = setup2.floor_h4(datetime.now(timezone.utc))
text = lambda v: None if v is None else str(v)
routes = {}
for symbol in COINS:
    settings = setup2_store.coin_settings(store, symbol)
    for market in ("perp", "spot"):
        h4 = setup2_store.proxied_bars(store, symbol, market, "4h", since=settings.anchor, until=until,
                                       settings=settings)
        series = setup2.anchored(h4, settings.anchor) or []
        rows = []
        for i, (bar, f) in enumerate(zip(series, indicator_series(series))):
            warm = i >= settings.warmup_bars - 1
            rows.append([bar.opened_at.isoformat(), str(bar.open), str(bar.high), str(bar.low), str(bar.close)]
                        + [text(f[k]) if warm else None for k in ("ema50", "ema200", "atr")])
        m15 = setup2_store.list_bars(store, symbol, market, "15m", since=settings.anchor, until=until)
        own_from = settings.proxy_until if market == "spot" else None
        routes[f"{symbol}:{market}"] = dict(
            own_from=text(own_from and own_from.isoformat()), h4=rows,
            m15=[[b.opened_at.isoformat(), str(b.open), str(b.high), str(b.low), str(b.close)] for b in m15])
        print(symbol, market, len(rows), len(m15), file=sys.stderr, flush=True)
json.dump(dict(exported_at=datetime.now(timezone.utc).isoformat(), until=until.isoformat(), routes=routes),
          sys.stdout)
