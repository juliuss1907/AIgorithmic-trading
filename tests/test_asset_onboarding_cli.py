import json
import sys
from datetime import datetime, timezone

from intraday.__main__ import main
from intraday.market_discovery import MarketScanner


def test_cli_add_scan_select_keeps_other_market_disabled(monkeypatch,capsys,tmp_path):
    monkeypatch.setattr(MarketScanner,"discover",lambda self,symbol,*,market,now,notional=1000:{
        "symbol":symbol,"market":market,"created_at":now.isoformat(),"venues":[
            {"venue":"bnb","environment":"demo","market":market,"symbol":symbol,
             "instrument":symbol,"base_asset":symbol[:-4],"quote_asset":"USDT",
             "status":"listed","selectable":True,"execution_available":True}]})
    database=str(tmp_path/"source.sqlite")
    def run(*args):
        monkeypatch.setattr(sys,"argv",["aigt","assets",*args,"--database",database])
        main()
        return json.loads(capsys.readouterr().out)
    added=run("add","DOGE","--market","spot")
    assert added["asset"]["symbol"]=="DOGEUSDT"
    scan=run("scan","DOGE","--market","spot")
    route=run("venue","set","DOGE","--market","spot","--venue","bnb","--environment","demo","--scan-id",scan["id"])
    assert route["market"]=="spot"
    status=run("status","DOGE")
    assert status["stages"]=={"spot_4h":"shadow"}
    assert status["execution_routes"]==[route]
