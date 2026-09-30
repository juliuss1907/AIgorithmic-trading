from datetime import datetime, timedelta, timezone

import pytest

from intraday.asset_onboarding import AssetOnboarding
from intraday.store import IntradayStore
from intraday.contracts import DecisionScope


NOW = datetime(2026,9,30,tzinfo=timezone.utc)


class Scanner:
    def discover(self,symbol,*,market,now,notional=1000):
        return {"symbol":symbol,"market":market,"created_at":now.isoformat(),"venues":[
            {"venue":"bnb","environment":"demo","market":market,"symbol":symbol,
             "instrument":symbol,"base_asset":symbol[:-4],"quote_asset":"USDT",
             "status":"listed","selectable":True,"execution_available":True}]}


def test_adding_selecting_and_scope_configuration_do_not_activate_trading(tmp_path):
    store=IntradayStore(tmp_path/"source.sqlite")
    service=AssetOnboarding(store,scanner=Scanner())
    service.add("DOGE",market="spot",now=NOW,request_id="add-1")
    scan=service.scan("DOGE",market="spot",now=NOW)
    route=service.select("DOGE",market="spot",venue="bnb",environment="demo",scan_id=scan["id"],now=NOW,request_id="select-1")
    assert route["instrument"]=="DOGEUSDT"
    assert store.asset_spec("DOGE").binance_spot_symbol=="DOGEUSDT"
    assert store.asset_spec("DOGE").binance_perp_symbol is None
    assert service.routes("DOGE")==[route]
    assert store.asset_lifecycle("DOGE", DecisionScope.SPOT_4H).stage.value=="shadow"
    assert service.select("DOGE",market="spot",venue="bnb",environment="demo",scan_id=scan["id"],now=NOW,request_id="select-1")==route
    with pytest.raises(ValueError,match="idempotency"):
        service.add("ETH",market="spot",now=NOW,request_id="add-1")


def test_stale_cross_market_and_non_executable_venue_selection_is_rejected(tmp_path):
    service=AssetOnboarding(IntradayStore(tmp_path/"source.sqlite"),scanner=Scanner())
    service.add("DOGE",market="spot",now=NOW)
    service.add("DOGE",market="perp",now=NOW)
    scan=service.scan("DOGE",market="spot",now=NOW)
    for changes in ({"market":"perp"},{"venue":"hl","environment":"testnet"}, {"now":NOW+timedelta(minutes=2)}):
        args=dict(market="spot",venue="bnb",environment="demo",scan_id=scan["id"],now=NOW)
        args.update(changes)
        with pytest.raises(ValueError):
            service.select("DOGE",**args)
    assert service.routes("DOGE")==[]
