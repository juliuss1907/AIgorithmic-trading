from fastapi.testclient import TestClient

from intraday.market_discovery import MarketScanner
from intraday.web import create_app


def test_onboarding_writes_require_control_and_do_not_activate(tmp_path,monkeypatch):
    monkeypatch.setattr(MarketScanner,"discover",lambda self,symbol,*,market,now,notional=1000:{
        "symbol":symbol,"market":market,"created_at":now.isoformat(),"venues":[
            {"venue":"bnb","environment":"demo","market":market,"symbol":symbol,
             "instrument":symbol,"base_asset":symbol[:-4],"quote_asset":"USDT",
             "status":"listed","selectable":True,"execution_available":True}]})
    client=TestClient(create_app(database=tmp_path/"source.sqlite",control_token="test-control"))
    assert client.get("/assets").status_code==200
    data={"symbol":"DOGE","market":"spot"}
    assert client.post("/api/assets",json=data,headers={"Idempotency-Key":"add-1"}).status_code==401
    auth={"Authorization":"Bearer test-control","Idempotency-Key":"add-1"}
    added=client.post("/api/assets",json=data,headers=auth)
    assert added.status_code==201
    assert client.post("/api/assets",json=data,headers=auth).json()==added.json()
    assert client.post("/api/assets",json={**data,"symbol":"SOL"},headers=auth).status_code==409
    scan=client.post("/api/asset-scans",json=data,headers={**auth,"Idempotency-Key":"scan-1"})
    assert scan.status_code==202
    report=client.get("/api/asset-scans/"+scan.json()["id"]).json()
    assert report["status"]=="completed"
    selected=client.put("/api/assets/DOGE/venues/spot",headers={**auth,"Idempotency-Key":"venue-1"},json={
        "venue":"bnb","environment":"demo","scan_id":report["id"]})
    assert selected.status_code==200
    assets=client.get("/api/assets?market=spot").json()["assets"]
    doge=next(a for a in assets if a["symbol"]=="DOGEUSDT")
    assert doge["stages"]=={"spot_4h":"shadow"}
    assert doge["execution_routes"][0]["venue"]=="bnb"
    assert client.get("/api/assets?market=perp").json()["assets"][-1]["symbol"]!="DOGEUSDT"


def test_disabled_controls_and_idempotency_validation(tmp_path):
    client=TestClient(create_app(database=tmp_path/"source.sqlite"))
    assert client.post("/api/assets",json={"symbol":"DOGE","market":"spot"},headers={"Idempotency-Key":"test"}).status_code==503
