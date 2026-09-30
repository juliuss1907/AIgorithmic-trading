"""Shared CLI/dashboard catalog workflow. Venue selection never enables execution."""

from datetime import datetime
from dataclasses import replace
import json
import uuid
import hashlib

from intraday.assets import spec_payload
from intraday.contracts import DecisionScope
from intraday.market_discovery import MarketScanner


class AssetOnboarding:
    def __init__(self,store,*,scanner=None):
        self.store=store
        self.scanner=scanner or MarketScanner()

    def add(self,symbol,*,market,now,request_id=None):
        return spec_payload(self.store.register_asset(symbol,market=market,now=now,request_id=request_id))

    def scan(self,symbol,*,market,now,notional=1000,scan_id=None):
        spec=self.store.asset_spec(symbol)
        scope=DecisionScope.SPOT_4H if market=="spot" else DecisionScope.PERP_INTRADAY
        if market not in {"spot","perp"} or scope not in spec.enabled_scopes:
            raise ValueError("market is not registered for this asset")
        result=self.scanner.discover(spec.symbol,market=market,now=now,notional=notional)
        result.update(id=scan_id or uuid.uuid4().hex,status="completed")
        with self.store._connect() as connection:
            connection.execute("INSERT INTO asset_market_scans VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload_json=excluded.payload_json",
                               (result["id"],spec.symbol,market,now.isoformat(),json.dumps(result,allow_nan=False)))
        return result

    def start_scan(self,symbol,*,market,now,notional,request_id):
        spec=self.store.asset_spec(symbol)
        scope=DecisionScope.SPOT_4H if market=="spot" else DecisionScope.PERP_INTRADAY
        if market not in {"spot","perp"} or scope not in spec.enabled_scopes:
            raise ValueError("market is not registered for this asset")
        request=json.dumps({"action":"scan","symbol":spec.symbol,"market":market,"notional":notional},sort_keys=True)
        scan_id=hashlib.sha256(("scan:"+request_id).encode()).hexdigest()[:32]
        result={"id":scan_id,"symbol":spec.symbol,"market":market,"status":"running","created_at":now.isoformat()}
        with self.store._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous=connection.execute("SELECT request_json,response_json FROM asset_catalog_requests WHERE id=?",(request_id,)).fetchone()
            if previous:
                if previous[0]!=request:
                    raise ValueError("idempotency key reused with a different payload")
                return json.loads(previous[1]),False
            connection.execute("INSERT INTO asset_market_scans VALUES (?,?,?,?,?)",(scan_id,spec.symbol,market,now.isoformat(),json.dumps(result)))
            connection.execute("INSERT INTO asset_catalog_requests VALUES (?,?,?)",(request_id,request,json.dumps(result)))
        return result,True

    def complete_scan(self,scan_id,*,notional,now):
        report=self.report(scan_id)
        try:
            self.scan(report["symbol"],market=report["market"],now=now,notional=notional,scan_id=scan_id)
        except Exception:
            report.update(status="failed",reason="public_scan_unavailable")
            with self.store._connect() as connection:
                connection.execute("UPDATE asset_market_scans SET payload_json=? WHERE id=?",(json.dumps(report),scan_id))

    def report(self,scan_id):
        with self.store._connect() as connection:
            row=connection.execute("SELECT payload_json FROM asset_market_scans WHERE id=?",(scan_id,)).fetchone()
        if not row:
            raise ValueError("unknown market scan")
        return json.loads(row[0])

    def routes(self,symbol=None):
        query="SELECT * FROM asset_venue_routes"
        params=()
        if symbol:
            query+=" WHERE symbol=?"
            params=(self.store.asset_spec(symbol).symbol,)
        with self.store._connect() as connection:
            return [dict(r) for r in connection.execute(query+" ORDER BY symbol,market",params)]

    def select(self,symbol,*,market,venue,environment,scan_id,now,request_id=None):
        spec=self.store.asset_spec(symbol)
        request={"action":"select","symbol":spec.symbol,"market":market,"venue":venue,
                 "environment":environment,"scan_id":scan_id}
        request_json=json.dumps(request,sort_keys=True)
        with self.store._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if request_id:
                previous=connection.execute("SELECT request_json,response_json FROM asset_catalog_requests WHERE id=?",(request_id,)).fetchone()
                if previous:
                    if previous[0]!=request_json:
                        raise ValueError("idempotency key reused with a different payload")
                    return json.loads(previous[1])
            saved=connection.execute("SELECT payload_json FROM asset_market_scans WHERE id=?",(scan_id,)).fetchone()
            if not saved:
                raise ValueError("unknown market scan")
            report=json.loads(saved[0])
            if (venue!="bnb" or environment!="demo" or report["symbol"]!=spec.symbol or report["market"]!=market
                    or not -2 <= (now-datetime.fromisoformat(report["created_at"])).total_seconds() <= 60):
                raise ValueError("venue selection requires a fresh same-market Binance Demo scan")
            row=next((r for r in report["venues"] if r["venue"]==venue and r["environment"]==environment),None)
            if (not row or row["status"]!="listed" or row.get("selectable") is not True
                    or row["instrument"]!=spec.symbol or row["quote_asset"]!="USDT" or row["base_asset"]!=spec.base_asset):
                raise ValueError("venue does not have a verified executable market")
            previous=connection.execute("SELECT * FROM asset_venue_routes WHERE symbol=? AND market=?",(spec.symbol,market)).fetchone()
            if previous and (previous["venue"],previous["environment"],previous["instrument"])!=(venue,environment,row["instrument"]):
                raise ValueError("route changes require paused, flat and reconciled execution")
            route={"symbol":spec.symbol,"market":market,"venue":venue,"environment":environment,
                   "instrument":row["instrument"],"scan_id":scan_id,"updated_at":now.isoformat()}
            connection.execute("INSERT INTO asset_venue_routes VALUES (?,?,?,?,?,?,?) ON CONFLICT(symbol,market) DO UPDATE SET scan_id=excluded.scan_id,updated_at=excluded.updated_at",
                               tuple(route.values()))
            spec=replace(spec,**{"binance_spot_symbol" if market=="spot" else "binance_perp_symbol":row["instrument"]})
            connection.execute("UPDATE asset_catalog SET payload_json=? WHERE symbol=?",
                               (json.dumps(spec_payload(spec),sort_keys=True),spec.symbol))
            if request_id:
                connection.execute("INSERT INTO asset_catalog_requests VALUES (?,?,?)",(request_id,request_json,json.dumps(route,sort_keys=True)))
        return route
