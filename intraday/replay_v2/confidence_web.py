"""Read-only confidence proposal projection; no provider, collector or writer."""

import sqlite3

from fastapi import APIRouter, HTTPException, Query

from intraday.assets import ticker_symbol
from intraday.contracts import DecisionScope
from intraday.replay_v2.confidence_reviews import ReviewStore
from intraday.replay_v2.study import STUDY_COINS, base_rule
from intraday.store import IntradayStore


def confidence_router(database, root):
    router = APIRouter()

    @router.get("/api/replay/confidence")
    def proposals(symbol: str | None = Query(None, max_length=24),
                  offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)):
        try:
            reader = IntradayStore(database, read_only=True)
            symbols = [ticker_symbol(symbol)] if symbol else list(STUDY_COINS)
            items = []
            with reader.read_snapshot():
                for coin in symbols[offset:offset+limit]:
                    reader.asset_spec(coin)
                    rule = base_rule(reader, coin, DecisionScope.PERP_INTRADAY)
                    items.append({"symbol":coin,
                        "current_threshold":rule.parameters.confidence_threshold if rule else None,
                        "current_rule_id":rule.rule_id if rule else None,
                        "current_rule_status":reader.scoped_rule_status(rule.rule_id) if rule else None,
                        "proposal":ReviewStore(root).latest(coin),
                        "research_only":True,"activation_allowed":False})
            return {"items":items,"total":len(symbols),"offset":offset,"limit":limit}
        except ValueError:
            raise HTTPException(400,"Invalid symbol or confidence evidence") from None
        except (OSError,sqlite3.Error):
            raise HTTPException(503,"Confidence evidence unavailable") from None

    return router
