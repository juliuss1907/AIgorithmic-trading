import json
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from intraday import learning_review as lr
from intraday import setup2, setup2_store
from intraday.execution.contracts import AccountRef, ExecutionFill, OrderIntent, OrderUpdate
from intraday.execution.journal import ExecutionJournal
from intraday.learning_cli import dispatch_learning
from intraday.store import IntradayStore

END = datetime(2026, 10, 19, tzinfo=timezone.utc)
START = END-timedelta(days=7)
H4 = setup2.H4
M15 = timedelta(minutes=15)
SPOT = AccountRef(venue="binance", environment="demo", account_id="test", market="spot")


def kline(opened, o, h, l, c, v=1000, width=H4):
    ms = int(opened.timestamp()*1000)
    return [ms, str(o), str(h), str(l), str(c), str(v), ms+int(width.total_seconds()*1000)-1]


def seasonal(store, symbol="ETHUSDT", market="perp", days=95):
    """+1% in every 08:00Z bar, -1% in every 20:00Z bar, flat otherwise."""
    rows, price, at = [], 100.0, END-timedelta(days=days)
    while at < END:
        move = {8: 0.01, 20: -0.01}.get(at.hour, 0.0)
        close = price*(1+move)
        rows.append(kline(at, price, max(price, close)*1.002, min(price, close)*0.998, close))
        price, at = close, at+H4
    setup2_store.record_candles(store, symbol, market, "4h", rows)
    seasonal_m15(store, symbol, days=days)


def seasonal_m15(store, symbol="ETHUSDT", days=95):
    """H1 pattern on M15 Perp candles: 08h +1%, then 09h -0.5% (a reversal of the rise), 20h -1%, flat otherwise.
    Each hour's move happens in its first quarter."""
    rows, price, at = [], 100.0, END-timedelta(days=days)
    while at < END:
        move = {8: 0.01, 9: -0.005, 20: -0.01}.get(at.hour, 0.0) if at.minute == 0 else 0.0
        close = price*(1+move)
        rows.append(kline(at, price, max(price, close), min(price, close), close, width=M15))
        price, at = close, at+M15
    setup2_store.record_candles(store, symbol, "perp", "15m", rows)


def observe(store, symbol, market, bar_close, *, side, close, atr="2", entry=False, donchian=False, exit=False,
            failed=(), blockers=(), status="skipped_no_setup"):
    observation = setup2.Setup2Observation(
        side=side, bar_open=bar_close-H4, bar_close=bar_close, entry=entry, exit=exit, donchian_entry=donchian,
        failed_filters=tuple(failed), blockers=tuple(blockers), close=D(str(close)), atr=D(atr),
        features={"atr": D(atr), "adx": D(25), "close": D(str(close))}, evaluator_version="setup2-slots-live-v1")
    setup2_store.record_observation(store, symbol, market, "rule-1", observation, active=True, jev_status=status,
                                    now=bar_close)


def order(journal, intent_id, *, at, side, price=None, source_id="setup2:rule-1:1", order_type="MARKET", stop=None,
          status="FILLED", quantity=D("2")):
    intent = OrderIntent(intent_id=intent_id, account=SPOT, symbol="ETHUSDT", market="spot", side=side,
                         quantity=quantity, order_type=order_type, stop_price=stop, source_id=source_id,
                         created_at=at)
    journal.prepare(intent)
    fills = () if price is None else (ExecutionFill(fill_id=intent_id+"-f", quantity=quantity, price=price,
                                                    commission=D("0.2"), commission_asset="USDT", filled_at=at),)
    journal.record(intent, OrderUpdate(intent_id=intent_id, status=status, received_at=at, average_price=price,
                                       executed_quantity=quantity if price else D(0), fills=fills))
    return intent


def news(store, event_id, received_at, title, severity="high"):
    payload = dict(event_id=event_id, received_at=received_at.isoformat(), title=title, category="market_risk",
                   severity=severity, source_id="coindesk")
    with store._connect() as c:
        c.execute("INSERT INTO news_events VALUES (?, ?, ?, ?)",
                  (event_id, "coindesk", received_at.isoformat(), json.dumps(payload)))


@pytest.fixture
def store(tmp_path):
    store = IntradayStore(tmp_path/"intraday.sqlite3")
    setup2_store.install(store)
    return store


def test_session_statistics_run_on_hourly_bars_over_seven_and_ninety_days(store):
    seasonal_m15(store)
    stats = lr._session_stats(store, ["ETHUSDT"], END)
    assert len(stats) == 24*2
    pump, dump, flat = (stats[f"session:ETHUSDT:{h}:90d"] for h in ("08", "20", "00"))
    assert pump["n"] == 90 and pump["mean_return_pct"] == pytest.approx(1.0) and pump["up_share"] == 1
    assert dump["mean_return_pct"] == pytest.approx(-1.0) and dump["up_share"] == 0
    assert flat["mean_return_pct"] == pytest.approx(0.0) and flat["session"] == "asia" and flat["t_stat"] is None
    assert pump["halves_agree"]["always"] is True and flat["halves_agree"]["always"] is False
    assert stats["session:ETHUSDT:08:7d"]["n"] == 7 and "halves_agree" not in stats["session:ETHUSDT:08:7d"]


def test_session_statistics_measure_reversal_of_the_previous_four_hours(store):
    seasonal_m15(store)
    stats = lr._session_stats(store, ["ETHUSDT"], END)
    turn = stats["session:ETHUSDT:09:90d"]             # always falls after the 08h rise
    assert turn["reversal_share"] == 1 and turn["reversal_z"] > 9
    assert turn["after_up"]["n"] == 90 and turn["after_up"]["mean_return_pct"] == pytest.approx(-0.5)
    assert turn["after_down"]["n"] == 0 and turn["halves_agree"]["after_up"] is True
    calm = stats["session:ETHUSDT:21:90d"]             # flat after the 20h fall: no reversal to count
    assert calm["after_down"]["n"] == 90 and calm["reversal_share"] is None


def test_a_demo_trade_carries_excursion_slippage_jev_and_only_earlier_news(store, tmp_path):
    entered = END-timedelta(days=3)
    bars = [(100, 104, 99, 102), (102, 108, 101, 106), (106, 106, 97, 103)]
    setup2_store.record_candles(store, "ETHUSDT", "spot", "4h",
                                [kline(entered+i*H4, *bar) for i, bar in enumerate(bars)])
    observe(store, "ETHUSDT", "spot", entered, side=1, close="99.5", entry=True, donchian=True, status="called")
    news(store, "before", entered-timedelta(hours=3), "ETH upgrade delayed")
    news(store, "after", entered+timedelta(hours=1), "ETH rallies")
    news(store, "other", entered-timedelta(hours=2), "Some altcoin listing", severity="low")
    journal = ExecutionJournal(tmp_path/"execution.sqlite3")
    entry = order(journal, "entry-1", at=entered, side="BUY", price=D(100))
    order(journal, "stop-1", at=entered, side="SELL", order_type="STOP_LOSS", stop=D(95), source_id=None, status="NEW")
    order(journal, "close-1", at=entered+3*H4, side="SELL", price=D(103),
          source_id="close:donchian_exit")
    journal.save_portfolio(SPOT.model_copy(update={"market": None}),
                           {"routes": {"spot:ETHUSDT": {"entry_id": entry.intent_id,
                                                        "entry_jev": {"would_allow": True, "confidence": 0.9}}}},
                           now=entered, kind="portfolio_entry_reserved")

    [trade] = lr._trades(store, tmp_path/"execution.sqlite3", START, END)

    assert trade["trade_id"] == "entry-1" and trade["status"] == "closed" and trade["exit_reason"] == "donchian_exit"
    assert trade["net_pnl_usdt"] == pytest.approx(6-0.4) and trade["net_return_pct"] == pytest.approx(5.6/200*100)
    assert trade["mfe_pct"] == pytest.approx(8) and trade["mae_pct"] == pytest.approx(-3)
    assert trade["giveback_pct"] == pytest.approx(5) and trade["stop_room_pct"] == pytest.approx(2)
    assert trade["entry_slippage_bps"] == pytest.approx((100/99.5-1)*1e4)
    assert trade["jev"] == {"would_allow": True, "confidence": 0.9}
    assert [n["id"] for n in trade["news_before"]] == ["before"]


def test_a_filtered_breakout_is_simulated_with_the_rule_trail_and_exit(store):
    trigger = START+timedelta(days=1)
    rows = [kline(trigger, 100, 101, 99, 100), kline(trigger+H4, 100, 102, 99.5, 101),
            kline(trigger+2*H4, 101, 103, 100, 102)]
    setup2_store.record_candles(store, "NEARUSDT", "perp", "4h", rows)
    observe(store, "NEARUSDT", "perp", trigger, side=-1, close=100, donchian=True, failed=["ema200"])
    observe(store, "NEARUSDT", "perp", trigger+2*H4, side=-1, close=101, exit=True)
    observe(store, "NEARUSDT", "perp", trigger+3*H4, side=-1, close=102)

    missed = lr._missed(store, [("NEARUSDT", "perp")], [], START, END)

    [item] = missed["items"]
    assert item["kind"] == "filtered" and item["failed_filters"] == ["ema200"]
    assert item["simulated"]["status"] == "donchian_exit"
    assert item["simulated"]["gross_return_pct"] == pytest.approx(-1.0)   # short from 100, out at 101
    assert missed["by_filter"]["filter:ema200"] == dict(blocked=1, closed=1, wins=0, mean_gross_return_pct=pytest.approx(-1.0))

    observe(store, "NEARUSDT", "perp", trigger-H4, side=-1, close=100, atr="0.2", donchian=True, failed=["adx"])
    stopped = {i["bar_close"]: i for i in lr._missed(store, [("NEARUSDT", "perp")], [], START, END)["items"]}
    # 3×ATR = 0.6 above 100: the first bar's high 101 breaches it.
    assert stopped[(trigger-H4).isoformat()]["simulated"]["status"] == "stop"


def test_confident_wrong_jev_answers_are_picked_against_the_next_bar(store):
    at = START+timedelta(days=2)
    setup2_store.record_candles(store, "ZECUSDT", "perp", "4h", [kline(at, 100, 104, 99, 103),
                                                                  kline(at+H4, 103, 104, 98, 99)])
    for decision, when, choice, confidence, rule in (("wrong", at, "Strong Sell", 0.9, "zec-setup2-slots"),
                                                     ("unsure", at, "Sell", 0.5, "perp-rule-v1"),
                                                     ("right", at+H4, "Sell", 0.95, "zec-setup2-slots")):
        answers = {"direction": {"choice": choice, "confidence": confidence,
                                 "probabilities": {choice: confidence, "Hold": round(1-confidence, 2)}}}
        with store._connect() as c:
            c.execute("INSERT INTO signals (decision_id, timestamp, symbol, scope, state_snapshot, raw_signals, "
                      "jev_answers, gate_passed, rules_version) VALUES (?, ?, 'ZECUSDT', 'perp_intraday', '{}', "
                      "'{\"rsi14\": 30}', ?, 0, ?)",
                      (decision, (when+timedelta(seconds=5)).isoformat(), json.dumps(answers), rule))

    jev = lr._jev(store, [], START, END)

    assert jev["confident_wrong"] == ["jev:wrong"]
    by_id = {d["id"]: d for d in jev["decisions"]}
    assert by_id["jev:wrong"]["truth"] == "long" and by_id["jev:right"]["truth"] == "short"
    assert by_id["jev:wrong"]["source"] == "setup2" and by_id["jev:unsure"]["source"] == "legacy"
    assert by_id["jev:wrong"]["inputs"] == {"rsi14": 30}
    assert jev["parameters"] == {"zec-setup2-slots": {}}     # no such rule stored: no settings to propose


def facts_for_review():
    return dict(
        routes=["ETHUSDT:perp"], chart={"ETHUSDT:perp": {}}, replay={},
        sessions={"session:ETHUSDT:08:7d": {},
                  "session:ETHUSDT:08:90d": dict(mean_return_pct=0.25, t_stat=4.0, after_up=dict(mean_return_pct=0.3,
                                                 t_stat=2.0), halves_agree=dict(always=True, after_up=True))},
        trades=[dict(trade_id="t1", news_before=[{"id": "n1"}])],
        missed=dict(items=[{"id": "missed:x"}], by_filter={"filter:ema200": {}}),
        jev=dict(decisions=[{"id": "jev:a"}],
                 parameters={"r1": {"confidence_threshold": dict(value=0.85, min=0.69, max=1.0)}}),
        next_week=dict(funding={"funding:ETHUSDT": {}}, correlations_30d={}, verified_news=[{"id": "cluster:c"}]),
        prior_proposals=[{"id": "lr-old-A3-1"}], operations={"heartbeat": {}})


def session_proposal(**changes):
    return dict(symbol="ETHUSDT", market="perp", side="long", condition="always", entry_hour_utc=8, exit_hour_utc=9,
                rationale="phiên Âu", invalidation="2 tuần lỗ", confidence=0.5,
                evidence=["session:ETHUSDT:08:90d"]) | changes


def answer(**changes):
    base = dict(
        chart=[dict(route="ETHUSDT:perp", summary="giảm", evidence=["ETHUSDT:perp"])],
        session_patterns=[dict(symbol="ETHUSDT", hour_utc=8, direction="pump", condition="always",
                               consistent_90d=True, confidence=0.6,
                               evidence=["session:ETHUSDT:08:7d", "session:ETHUSDT:08:90d"])],
        session_proposals=[session_proposal()],
        trades=[dict(trade_id="t1", cause="prior_event", explanation="tin xấu", evidence=["n1"])],
        trade_proposals=[], exits=[], exit_proposals=[],
        filters=[dict(filter="filter:ema200", verdict="too_few", explanation="ít mẫu", evidence=["filter:ema200"])],
        filter_proposals=[],
        jev_patterns=[], jev_proposals=[dict(target="parameter", rule_id="r1", name="confidence_threshold", current="0.85",
                                             proposed="0.9", expected_effect="bớt sai", avoided=["jev:a"],
                                             evidence=["jev:a"])],
        next_week=[dict(title="tin", severity="low", detail="x", evidence=["cluster:c"])],
        prior=[dict(proposal_id="lr-old-A3-1", verdict="too_early", explanation="chưa đủ", evidence=[])],
        limitations="")
    base.update(changes)
    return lr.LearningReview.model_validate(base)


def test_a_review_citing_only_known_facts_passes():
    assert lr.validate_review(answer(), facts_for_review()) == []


@pytest.mark.parametrize("changes, error", [
    (dict(chart=[dict(route="ETHUSDT:perp", summary="x", evidence=["made-up"])]), "unknown evidence made-up"),
    (dict(session_proposals=[session_proposal(market="spot", side="short")]), "Spot cannot be short"),
    (dict(session_proposals=[session_proposal(entry_hour_utc=9, exit_hour_utc=10, invalidation="",
                                              evidence=["session:ETHUSDT:08:7d"])]), "no 90-day session evidence"),
    (dict(session_proposals=[session_proposal(exit_hour_utc=12)]), "one-hour trade"),
    (dict(trades=[]), "every trade must be reviewed"),
    (dict(trades=[dict(trade_id="t1", cause="prior_event", explanation="x", evidence=["jev:a"])]),
     "prior_event without prior news"),
    (dict(jev_proposals=[dict(target="parameter", rule_id="r1", name="confidence_threshold", current="0.85",
                              proposed="0.60", expected_effect="x", avoided=[], evidence=[])]), "outside"),
    (dict(jev_proposals=[dict(target="parameter", rule_id="r1", name="toxic_flow_max", current="0.3",
                              proposed="0.2", expected_effect="x", avoided=[], evidence=[])]), "unknown setting"),
    (dict(prior=[dict(proposal_id="lr-unknown", verdict="keep", explanation="x", evidence=[])]), "unknown proposal"),
])
def test_a_review_is_rejected_when_it_breaks_a_bound(changes, error):
    errors = lr.validate_review(answer(**changes), facts_for_review())
    assert any(error in e for e in errors), errors


@pytest.mark.parametrize("changes, reason", [
    (dict(condition="after_up"), "|t_stat| below 3"),
    (dict(condition="after_down"), "|t_stat| below 3"),
    (dict(side="short"), "side against the mean"),
    (dict(market="spot"), "within round-trip cost"),
])
def test_weak_session_proposals_are_screened_out_without_voiding_the_review(changes, reason):
    review = lr.review_week(FakeClient(answer(session_proposals=[session_proposal(), session_proposal(**changes)])),
                            facts_for_review(), now=END)
    assert review["status"] == "valid" and len(review["answer"]["session_proposals"]) == 1
    assert [s["key"] for s in review["screened"]] == ["session:ETHUSDT:08:90d"]
    assert any(reason in r for r in review["screened"][0]["reasons"])
    assert "Code loại 1 đề xuất phiên" in lr.telegram_summary("lr-1", dict(
        window=dict(start="2026-10-12", end="2026-10-19"), results=dict(closed=0, wins=0, net_pnl_usdt=0),
        jev=dict(confident_wrong=[], agreed_losing_trades=[])), review, [])


class FakeClient:
    model_ref = "fake@model"

    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def complete(self, **request):
        self.calls.append(request)
        return self.reply


def test_review_week_sends_no_operations_and_reports_validity():
    facts = facts_for_review()
    client = FakeClient(answer())
    result = lr.review_week(client, facts, now=END)
    assert result["status"] == "valid" and result["model"] == "fake@model"
    assert "operations" not in client.calls[0]["input_payload"] and client.calls[0]["max_tokens"] == 12_000
    bad = lr.review_week(FakeClient(answer(trades=[])), facts, now=END)
    assert bad["status"] == "review_invalid" and bad["errors"]


def test_proposals_are_queued_pending_and_decided_once(tmp_path):
    added = lr.record_proposals(tmp_path, "lr-1", answer().model_dump(), now=END)
    assert [p["id"] for p in added] == ["lr-1-A3-1", "lr-1-E2-1"]
    assert {p["status"] for p in lr.load_proposals(tmp_path)} == {"pending"}
    decided = lr.decide_proposal(tmp_path, "lr-1-A3-1", status="approved", actor="julius", note="thử", now=END)
    assert decided["status"] == "approved" and decided["actor"] == "julius"
    with pytest.raises(ValueError, match="already approved"):
        lr.decide_proposal(tmp_path, "lr-1-A3-1", status="rejected", actor="julius", note=None, now=END)
    prior = lr._prior(lr.load_proposals(tmp_path), [dict(trade_id="s1", source_id="session:lr-1-A3-1:x")])
    assert [(p["id"], p["session_trades"]) for p in prior] == [("lr-1-A3-1", ["s1"])]


def test_cli_writes_a_facts_only_report_from_a_bare_database(store, tmp_path, capsys):
    seasonal(store)
    observe(store, "ETHUSDT", "perp", END-H4, side=-1, close=100)
    arguments = Namespace(learning_command="review", database=str(store.database), execution_database=None,
                          end=END, days=7, profile=None, no_llm=True, notify=False, output=tmp_path/"reports")
    dispatch_learning(arguments)
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "facts_only" and "Chưa chạy LLM" in result["summary"]
    report = json.loads(open(result["report"]).read())
    assert report["facts"]["routes"] == ["ETHUSDT:perp"] and report["review"] is None
    assert report["facts"]["sessions"]["session:ETHUSDT:08:90d"]["n"] == 90
