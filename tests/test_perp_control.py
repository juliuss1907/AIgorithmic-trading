from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from intraday.execution.contracts import AccountRef, AccountSnapshot, Position
from intraday.execution.journal import ExecutionJournal


NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)
ACCOUNT = AccountRef(venue="binance", environment="demo", account_id="fixture")


class Source:
    def __init__(self, symbol="ETHUSDT"):
        self.symbol = symbol
    def route(self):
        return {"symbol": self.symbol, "market": "perp", "instrument": self.symbol,
                "venue": "bnb", "environment": "demo", "updated_at": NOW.isoformat()}


class Venue:
    def __init__(self, symbol="ETHUSDT"):
        self.symbol = symbol
        self.account_ref = ACCOUNT.model_copy(update={"market": "perp"})
        self.leverage = 3
        self.positions = ()
        self.open_orders = ()
        self.changes = []
        self.fail_after_change = False
    def check_clock(self, *, now):
        pass
    def account_snapshot(self, *, now):
        return AccountSnapshot(account=self.account_ref, wallet_balance=5000, equity=5000,
            available_balance=5000, positions=self.positions, open_orders=self.open_orders,
            can_trade=True, one_way=True, single_asset=True, margin_mode="ISOLATED",
            leverage=self.leverage, observed_at=now)
    def leverage_limit(self, notional=Decimal(0)):
        return 10
    def set_leverage(self, leverage):
        self.changes.append(leverage)
        self.leverage = leverage
        if self.fail_after_change:
            raise OSError("simulated lost acknowledgement")


def controller(tmp_path, *, journal=True):
    from intraday.execution.perp_control import PerpController
    store = ExecutionJournal(tmp_path / "execution.sqlite") if journal else None
    venue = Venue()
    return PerpController(Source(), venue, store, clock=lambda: NOW), venue


def test_perp_information_is_read_only_and_does_not_adopt_positions(tmp_path):
    control, venue = controller(tmp_path, journal=False)
    venue.positions = (Position(symbol="ETHUSDT", quantity=-2, entry_price=100, mark_price=90),)
    report = control.information()
    assert report["symbol"] == "ETHUSDT"
    assert report["actual_leverage"] == report["configured_leverage"] == 3
    assert report["positions"][0]["side"] == "SHORT"
    assert report["positions"][0]["unrealized_pnl"] == "20"
    assert report["positions"][0]["managed_by_aigt"] is False
    assert not venue.changes and not (tmp_path / "execution.sqlite").exists()


def test_perp_parser_accepts_both_leverage_spellings():
    from intraday.__main__ import _parser
    for option in ("-leverage", "--leverage"):
        args = _parser().parse_args(["perp", "ETH", option])
        assert args.symbol == "ETHUSDT" and args.leverage
    assert not _parser().parse_args(["perp", "ETH"]).leverage


def test_confirmed_change_is_persisted_verified_and_idempotent(tmp_path):
    control, venue = controller(tmp_path)
    preview = control.preview(5)
    assert not venue.changes
    request = control.request_change(preview, request_id="test-confirm-1")
    assert request["status"] == "queued"
    result = control.process(request["id"])
    assert result["status"] == "verified"
    assert venue.changes == [5]
    assert control.information()["configured_leverage"] == 5
    assert control.request_change(preview, request_id="test-confirm-1")["status"] == "verified"
    assert control.process(request["id"])["status"] == "verified"
    assert venue.changes == [5]
    assert not control.journal.portfolio(ACCOUNT)


@pytest.mark.parametrize("value", [0, 11, 2.5, True, "5", -1])
def test_leverage_target_must_be_integer_in_operator_range(tmp_path, value):
    control, venue = controller(tmp_path)
    with pytest.raises(ValueError):
        control.preview(value)
    assert not venue.changes


def test_confirmation_drift_refuses_mutation(tmp_path):
    control, venue = controller(tmp_path)
    request = control.request_change(control.preview(5), request_id="drift-1")
    venue.leverage = 4
    assert control.process(request["id"])["status"] == "failed"
    assert not venue.changes


def test_live_position_and_running_campaign_block_changes(tmp_path):
    control, venue = controller(tmp_path)
    venue.positions = (Position(symbol="ETHUSDT", quantity=1, entry_price=100, mark_price=100),)
    with pytest.raises(ValueError, match="flat"):
        control.preview(5)
    venue.positions = ()
    control.journal.save_portfolio(ACCOUNT, {"paused": False}, now=NOW, kind="fixture")
    with pytest.raises(ValueError, match="pause"):
        control.preview(5)
    assert not venue.changes


def test_lost_ack_is_reconciled_without_resubmitting(tmp_path):
    control, venue = controller(tmp_path)
    venue.fail_after_change = True
    request = control.request_change(control.preview(5), request_id="lost-ack-1")
    assert control.process(request["id"])["status"] == "unknown"
    assert control.process(request["id"])["status"] == "verified"
    assert venue.changes == [5]


def test_stale_confirmation_and_competing_request_fail(tmp_path):
    control, venue = controller(tmp_path)
    preview = control.preview(5)
    control.clock = lambda: NOW + timedelta(seconds=61)
    with pytest.raises(ValueError, match="expired"):
        control.request_change(preview, request_id="expired-1")
    control.clock = lambda: NOW
    control.request_change(preview, request_id="first-1")
    with pytest.raises(ValueError, match="pending"):
        control.request_change(control.preview(4), request_id="second-1")
    with pytest.raises(ValueError, match="different"):
        control.request_change(control.preview(4), request_id="first-1")


def test_settings_permission_is_not_order_permission():
    from intraday.execution.binance_demo import DemoCredentials, DemoTransport, BinanceDemoAdapter
    from intraday.execution.contracts import ExecutionUnavailable
    from urllib.parse import urlsplit, parse_qs
    calls = []
    def send(request):
        url = urlsplit(request.full_url)
        assert url.netloc == "demo-fapi.binance.com"
        calls.append((request.method, url.path))
        if url.path.endswith("leverageBracket"):
            return [{"symbol": "ETHUSDT", "brackets": [{"notionalFloor": 0, "notionalCap": 10000, "initialLeverage": 20}]}]
        return {"symbol": "ETHUSDT", "leverage": int(parse_qs(url.query)["leverage"][0]), "maxNotionalValue": "10000"}
    creds = DemoCredentials(api_key="fake", api_secret="fake")
    read = BinanceDemoAdapter(DemoTransport(creds, send=send), symbol="ETHUSDT", market_scoped=True)
    with pytest.raises(ExecutionUnavailable):
        read.set_leverage(5)
    venue = BinanceDemoAdapter(DemoTransport(creds, send=send, settings_enabled=True), symbol="ETHUSDT", market_scoped=True)
    assert venue.leverage_limit() == 20
    venue.set_leverage(5)
    with pytest.raises(ExecutionUnavailable):
        venue.transport.request("POST", "/fapi/v1/order", signed=True)
    with pytest.raises(ValueError):
        venue.transport.request("POST", "/fapi/v1/accountConfig", signed=True)
    assert calls == [("GET", "/fapi/v1/leverageBracket"), ("POST", "/fapi/v1/leverage")]


def test_cli_cancel_does_not_create_a_journal_or_change_settings(tmp_path, monkeypatch, capsys):
    from intraday.execution import perp_cli
    args = SimpleNamespace(symbol="ETHUSDT", leverage=True, source_database=tmp_path/"source.sqlite",
        execution_database=tmp_path/"execution.sqlite", secrets_file=tmp_path/"demo.json")
    source = Source()
    source.path = args.source_database
    venue = Venue()
    monkeypatch.setattr(perp_cli, "ScopedEvidenceSource", lambda *a, **kw: source)
    monkeypatch.setattr(perp_cli.DemoCredentials, "load", lambda path: object())
    monkeypatch.setattr(perp_cli, "DemoTransport", lambda *a, **kw: object())
    monkeypatch.setattr(perp_cli, "BinanceDemoAdapter", lambda *a, **kw: venue)
    monkeypatch.setattr(perp_cli, "enter_leverage", lambda: 5)
    monkeypatch.setattr(perp_cli, "confirm_leverage", lambda preview: False)
    perp_cli.dispatch_perp(args)
    assert not venue.changes and not args.execution_database.exists()
    assert "cancel" in capsys.readouterr().out.lower()


def test_legacy_running_state_is_visible_and_blocks_change(tmp_path):
    control, venue = controller(tmp_path)
    control.journal.save_control(ACCOUNT, {"paused": False}, now=NOW, kind="fixture")
    assert control.information()["aigt_state"] == "running"
    with pytest.raises(ValueError, match="pause"):
        control.preview(5)
    assert not venue.changes


def test_algo_order_blocks_change_and_expired_queue_never_submits(tmp_path):
    from intraday.execution.contracts import OpenOrder
    control, venue = controller(tmp_path)
    venue.open_orders = (OpenOrder(symbol="ETHUSDT", client_id="fixture-stop", order_type="STOP_MARKET"),)
    with pytest.raises(ValueError, match="flat"):
        control.preview(5)
    venue.open_orders = ()
    item = control.request_change(control.preview(5), request_id="expired-queue")
    control.clock = lambda: NOW+timedelta(seconds=61)
    assert control.process(item["id"])["status"] == "failed"
    assert not venue.changes


def test_crash_after_claim_with_wrong_leverage_never_resubmits(tmp_path):
    control, venue = controller(tmp_path)
    item = control.request_change(control.preview(5), request_id="crashed-claim")
    item["status"] = "applying"
    control.journal.save_settings_request(item, now=NOW)
    for _ in range(2):
        assert control.process(item["id"])["status"] == "unknown"
    assert not venue.changes
    assert control.journal.perp_setting(ACCOUNT, "ETHUSDT")["leverage"] == 3


def test_verified_readback_requires_unchanged_account_modes(tmp_path):
    control, venue = controller(tmp_path)
    original = venue.account_snapshot
    venue.account_snapshot = lambda **kw: original(**kw).model_copy(update={"margin_mode":"CROSSED"}) if venue.changes else original(**kw)
    item = control.request_change(control.preview(5), request_id="readback-mode-drift")
    assert control.process(item["id"])["status"] == "unknown"
    assert control.journal.perp_setting(ACCOUNT, "ETHUSDT")["leverage"] == 3


def test_read_only_information_does_not_modify_execution_evidence(tmp_path):
    import hashlib
    control, venue = controller(tmp_path)
    path = control.journal.path
    before = (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mode)
    control.journal = ExecutionJournal(path, read_only=True)
    assert control.information()["actual_leverage"] == 3
    assert (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mode) == before
    assert not venue.changes
