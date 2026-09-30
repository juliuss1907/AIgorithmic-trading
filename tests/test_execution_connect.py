import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from intraday.__main__ import _parser, main
from intraday.execution.binance_demo import DemoCredentials
from intraday.execution.contracts import AccountSnapshot, ExecutionUnavailable
from intraday.execution.journal import ExecutionJournal
from intraday.store import IntradayStore


def test_demo_execution_uses_connect_default_file():
    args = _parser().parse_args(["execution", "demo", "preflight", "--source-database", "source.sqlite3"])
    assert args.secrets_file == Path("state/execution-secrets/binance-demo.json")


@pytest.mark.parametrize("suffix", [["bnb"], ["hl"], ["hl", "demo"]])
def test_reserved_venues_do_not_prompt_or_initialize_state(monkeypatch, suffix):
    monkeypatch.setattr(IntradayStore, "__init__", lambda *_: pytest.fail("source DB touched"))
    monkeypatch.setattr(sys, "argv", ["aigt", "connect", *suffix])
    with pytest.raises(SystemExit, match="not supported"):
        main()


def test_first_connect_saves_private_file_after_probe(monkeypatch, tmp_path, capsys):
    from intraday.execution import connect
    path = tmp_path / "secrets" / "demo.json"
    answers = iter(["fake-key-long", "fake-secret-long"])
    monkeypatch.setattr(connect, "_prompt", lambda *a, **k: next(answers))
    monkeypatch.setattr(connect, "probe_demo", lambda _: {"status": "connected", "configuration_warnings": ["isolated 3x required"]})
    monkeypatch.setattr(IntradayStore, "__init__", lambda *_: pytest.fail("source DB touched"))
    monkeypatch.setattr(sys, "argv", ["aigt", "connect", "bnb", "demo", "--secrets-file", str(path)])
    main()
    assert DemoCredentials.load(path).api_key.get_secret_value() == "fake-key-long"
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    output = capsys.readouterr().out
    assert '"status": "connected"' in output
    assert "fake-key-long" not in output and "fake-secret-long" not in output


@pytest.fixture
def wizard(monkeypatch, tmp_path):
    from intraday.execution import connect
    path = tmp_path / "secrets" / "demo.json"
    path.parent.mkdir(mode=0o700)
    path.write_text(json.dumps({"api_key": "fake-key-long", "api_secret": "fake-secret-long"}))
    path.chmod(0o600)
    args = _parser().parse_args(["connect", "bnb", "demo", "--secrets-file", str(path),
                                "--execution-database", str(tmp_path / "journal.sqlite3")])
    report = {"status": "connected", "positions": 0, "open_orders": 0, "configuration_warnings": []}
    monkeypatch.setattr(connect, "probe_demo", lambda _: dict(report))
    monkeypatch.setattr(IntradayStore, "__init__", lambda *_: pytest.fail("source DB touched"))
    return connect, args, path, report


def answers(monkeypatch, connect, values):
    iterator = iter(values)
    monkeypatch.setattr(connect, "_prompt", lambda *a, **k: next(iterator))


def test_keep_default_preserves_file_and_masks_key(monkeypatch, wizard, capsys):
    connect, args, path, _ = wizard
    before = path.read_bytes()
    answers(monkeypatch, connect, [""])
    assert connect.dispatch_connect(args)["status"] == "connected"
    assert path.read_bytes() == before
    assert not args.execution_database.exists()
    output = capsys.readouterr().out
    assert "fake... ✓" in output
    assert "fake-key-long" not in output and "fake-secret-long" not in output


@pytest.mark.parametrize("namespace",["portfolio","spot","perp"])
def test_rotation_cannot_bypass_active_multi_market_namespaces(monkeypatch,wizard,namespace):
    connect,args,path,_ = wizard
    account = DemoCredentials.load(path).account_ref
    journal = ExecutionJournal(args.execution_database)
    if namespace=="portfolio":
        journal.save_portfolio(account,{"paused":False},now=datetime.now(timezone.utc),kind="fixture")
    else:
        journal.save_control(account.model_copy(update={"market":namespace}),{"paused":False},now=datetime.now(timezone.utc),kind="fixture")
    answers(monkeypatch,connect,["R"])
    before=path.read_bytes()
    with pytest.raises(SystemExit,match="pause"):
        connect.dispatch_connect(args)
    assert path.read_bytes()==before


def test_rotation_requires_both_market_reads_even_if_one_market_is_connected(monkeypatch,wizard):
    connect,args,path,report = wizard
    report["markets"]={"spot":{"status":"connected"},"perp":{"status":"unavailable"}}
    answers(monkeypatch,connect,["C"])
    with pytest.raises(SystemExit,match="both Spot and Perp"):
        connect.dispatch_connect(args)
    assert path.exists()


def test_exchange_connect_bypasses_registered_docker(monkeypatch, wizard):
    from intraday import __main__ as entry
    connect, args, path, _ = wizard
    answers(monkeypatch, connect, ["K"])
    monkeypatch.setattr(entry.deployment_cli, "load_deployment", lambda: pytest.fail("Docker routing used"))
    monkeypatch.setattr(sys, "argv", ["aigt", "connect", "bnb", "demo", "--secrets-file", str(path)])
    main()


@pytest.mark.parametrize("choice", ["R", "C"])
def test_change_requires_confirmation_and_preserves_journal(monkeypatch, wizard, choice):
    connect, args, path, _ = wizard
    journal = ExecutionJournal(args.execution_database)
    account = DemoCredentials.load(path).account_ref
    journal.save_control(account, {"enabled": True, "paused": True}, kind="test", now=datetime.now(timezone.utc))
    before = path.read_bytes()
    db_before = args.execution_database.read_bytes()
    answers(monkeypatch, connect, [choice, "n"])
    assert connect.dispatch_connect(args)["status"] == "cancelled"
    assert path.read_bytes() == before
    assert args.execution_database.read_bytes() == db_before


def test_replace_checks_new_key_and_keeps_old_campaign_paused(monkeypatch, wizard):
    connect, args, path, _ = wizard
    journal = ExecutionJournal(args.execution_database)
    old = DemoCredentials.load(path).account_ref
    journal.save_control(old, {"enabled": True, "paused": True}, kind="test", now=datetime.now(timezone.utc))
    db_before = args.execution_database.read_bytes()
    answers(monkeypatch, connect, ["r", "y", "next-key-long", "next-secret-long"])
    result = connect.dispatch_connect(args)
    assert result["reactivation_required"]
    assert DemoCredentials.load(path).api_key.get_secret_value() == "next-key-long"
    assert path.stat().st_mode & 0o777 == 0o600
    assert args.execution_database.read_bytes() == db_before
    assert journal.control(old)["paused"]


def test_clear_removes_only_credential_file(monkeypatch, wizard):
    connect, args, path, _ = wizard
    journal = ExecutionJournal(args.execution_database)
    account = DemoCredentials.load(path).account_ref
    journal.save_control(account, {"paused": True}, kind="test", now=datetime.now(timezone.utc))
    db_before = args.execution_database.read_bytes()
    answers(monkeypatch, connect, ["c", "y"])
    report = connect.dispatch_connect(args)
    assert report["status"] == "cleared" and not report["binance_key_revoked"]
    assert not path.exists() and args.execution_database.read_bytes() == db_before


@pytest.mark.parametrize("choice", ["R", "C"])
def test_change_blocked_when_campaign_not_paused(monkeypatch, wizard, choice):
    connect, args, path, _ = wizard
    journal = ExecutionJournal(args.execution_database)
    journal.save_control(DemoCredentials.load(path).account_ref, {"paused": False},
                         kind="test", now=datetime.now(timezone.utc))
    before = path.read_bytes()
    answers(monkeypatch, connect, [choice])
    with pytest.raises(SystemExit, match="pause and flatten"):
        connect.dispatch_connect(args)
    assert path.read_bytes() == before


@pytest.mark.parametrize("field", ["positions", "open_orders"])
@pytest.mark.parametrize("choice", ["R", "C"])
def test_change_blocked_by_live_positions_or_orders(monkeypatch, wizard, field, choice):
    connect, args, path, report = wizard
    report[field] = 1
    before = path.read_bytes()
    answers(monkeypatch, connect, [choice])
    with pytest.raises(SystemExit, match="open positions/orders"):
        connect.dispatch_connect(args)
    assert path.read_bytes() == before


def test_unresolved_intent_blocks_clear(monkeypatch, wizard):
    from intraday.execution.contracts import OrderIntent
    connect, args, path, _ = wizard
    journal = ExecutionJournal(args.execution_database)
    account = DemoCredentials.load(path).account_ref
    journal.save_control(account, {"paused": True}, kind="test", now=datetime.now(timezone.utc))
    journal.prepare(OrderIntent(intent_id="pending", account=account, symbol="BTCUSDT", market="perp",
                                side="BUY", quantity=".002", created_at=datetime.now(timezone.utc)))
    before = path.read_bytes()
    answers(monkeypatch, connect, ["C"])
    with pytest.raises(SystemExit, match="unresolved"):
        connect.dispatch_connect(args)
    assert path.read_bytes() == before


def test_journal_lock_blocks_rotation(monkeypatch, wizard):
    connect, args, path, _ = wizard
    journal = ExecutionJournal(args.execution_database)
    before = path.read_bytes()
    answers(monkeypatch, connect, ["R"])
    with journal.lock(), pytest.raises(SystemExit, match="another execution"):
        connect.dispatch_connect(args)
    assert path.read_bytes() == before


def test_terminal_order_with_unreconciled_fills_blocks_clear(monkeypatch, wizard):
    from intraday.execution.contracts import OrderIntent, OrderUpdate
    connect, args, path, _ = wizard
    journal = ExecutionJournal(args.execution_database)
    account = DemoCredentials.load(path).account_ref
    now = datetime.now(timezone.utc)
    order = OrderIntent(intent_id="partial", account=account, symbol="BTCUSDT", market="perp",
                        side="BUY", quantity=".002", created_at=now)
    journal.prepare(order)
    journal.record(order, OrderUpdate(intent_id="partial", status="CANCELED", executed_quantity=".001", received_at=now))
    before = path.read_bytes()
    answers(monkeypatch, connect, ["C"])
    with pytest.raises(SystemExit, match="unresolved"):
        connect.dispatch_connect(args)
    assert path.read_bytes() == before


def test_credential_lock_blocks_second_connect(monkeypatch, wizard):
    from intraday.execution.credential_file import credential_lock
    connect, args, path, _ = wizard
    with credential_lock(path), pytest.raises(SystemExit, match="another execution or connect"):
        connect.dispatch_connect(args)


@pytest.mark.parametrize("choice", ["K", "R", "C"])
def test_api_failure_preserves_existing_file_without_leaking(monkeypatch, wizard, capsys, choice):
    connect, args, path, _ = wizard
    before = path.read_bytes()
    answers(monkeypatch, connect, [choice])
    def fail(_):
        raise ExecutionUnavailable("fake-secret-long signature=private")
    monkeypatch.setattr(connect, "probe_demo", fail)
    with pytest.raises(SystemExit) as error:
        connect.dispatch_connect(args)
    assert path.read_bytes() == before
    assert "signature" not in str(error.value) and "fake-secret-long" not in str(error.value)
    assert "fake-secret-long" not in capsys.readouterr().out


def test_failed_new_key_validation_does_not_replace_old(monkeypatch, wizard):
    connect, args, path, report = wizard
    before = path.read_bytes()
    answers(monkeypatch, connect, ["R", "y", "next-key-long", "next-secret-long"])
    def probe(credentials):
        if credentials.api_key.get_secret_value() == "next-key-long":
            raise ExecutionUnavailable("invalid new key")
        return report
    monkeypatch.setattr(connect, "probe_demo", probe)
    with pytest.raises(SystemExit):
        connect.dispatch_connect(args)
    assert path.read_bytes() == before
    assert not list(path.parent.glob(".credential-*"))


@pytest.mark.parametrize("exception", [KeyboardInterrupt, EOFError])
def test_cancel_during_replace_preserves_old(monkeypatch, wizard, exception):
    connect, args, path, _ = wizard
    before = path.read_bytes()
    iterator = iter(["R", "y"])
    def interrupted(*a, **k):
        try:
            return next(iterator)
        except StopIteration:
            raise exception
    monkeypatch.setattr(connect, "_prompt", interrupted)
    with pytest.raises(SystemExit, match="cancelled"):
        connect.dispatch_connect(args)
    assert path.read_bytes() == before


def test_first_connect_failure_does_not_save(monkeypatch, wizard):
    connect, args, path, _ = wizard
    args.secrets_file = path.parent / "new.json"
    answers(monkeypatch, connect, ["next-key-long", "next-secret-long"])
    monkeypatch.setattr(connect, "probe_demo", lambda _: (_ for _ in ()).throw(ExecutionUnavailable("network failed")))
    with pytest.raises(SystemExit):
        connect.dispatch_connect(args)
    assert not args.secrets_file.exists()


def test_symlink_and_nonprivate_files_are_refused(monkeypatch, wizard):
    connect, args, path, _ = wizard
    link = path.parent / "link.json"
    link.symlink_to(path)
    args.secrets_file = link
    with pytest.raises(SystemExit, match="symlink"):
        connect.dispatch_connect(args)
    args.secrets_file = path
    path.chmod(0o644)
    with pytest.raises(SystemExit, match="connection failed"):
        connect.dispatch_connect(args)


def test_same_database_and_secret_path_is_refused(wizard):
    connect, args, path, _ = wizard
    args.execution_database = path
    before = path.read_bytes()
    with pytest.raises(SystemExit, match="separate"):
        connect.dispatch_connect(args)
    assert path.read_bytes() == before


def test_existing_nonprivate_directory_is_not_chmodded(wizard):
    connect, args, path, _ = wizard
    path.parent.chmod(0o755)
    before = path.read_bytes()
    with pytest.raises(SystemExit, match="directory must be owned and 0700"):
        connect.dispatch_connect(args)
    assert path.parent.stat().st_mode & 0o777 == 0o755
    assert path.read_bytes() == before
    assert not Path(str(path) + ".lock").exists()


def test_noninteractive_input_never_uses_echo_fallback(monkeypatch):
    from intraday.execution import connect
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(connect, "prompt", lambda *a, **k: pytest.fail("unsafe fallback"))
    with pytest.raises(connect.ConnectError, match="interactive terminal"):
        connect._prompt("Key: ", password=True)


def test_probe_uses_read_only_transport_and_separates_configuration(monkeypatch):
    from intraday.execution import connect
    credentials = DemoCredentials(api_key="fake-key-long", api_secret="fake-secret-long")
    snapshot = AccountSnapshot(account=credentials.account_ref, wallet_balance="5000", equity="5000",
        available_balance="5000", positions=(), open_orders=(), can_trade=False, one_way=False,
        single_asset=False, margin_mode="ISOLATED", leverage=5, observed_at=datetime.now(timezone.utc))
    class Venue:
        def __init__(self, transport):
            assert transport.writes_enabled is False
        def check_clock(self, **kwargs):
            pass
        def account_snapshot(self, **kwargs):
            return snapshot
    monkeypatch.setattr(connect, "BinanceDemoAdapter", Venue)
    report = connect.probe_demo(credentials)
    assert report["status"] == "connected" and report["leverage"] == 5
    assert len(report["configuration_warnings"]) == 4
    assert not report["trading_activated_by_connect"] and not report["order_permission_verified"]
