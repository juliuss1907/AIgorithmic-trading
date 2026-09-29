from datetime import datetime, timedelta, timezone
import sqlite3
import pytest

from intraday.contracts import (
    DecisionScope, FeatureSnapshot, ScopedRuleCandidate, SpotRuleParameters,
    SpotRuleProposal,
)
from intraday.store import IntradayStore
from intraday.asset_rule_lifecycle import (
    activate_asset_spot_rule,
    bootstrap_asset_spot_rule,
    replay_asset_spot_rule,
    start_asset_spot_soak,
    evaluate_asset_spot_soak,
    propose_asset_spot_rule,
)
from intraday.scoped_rule_lifecycle import ScopedRuleEvaluation


NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)


def _rule(symbol: str) -> ScopedRuleCandidate:
    return ScopedRuleCandidate.create(
        rule_id=f"{symbol.lower()}-spot-baseline",
        parent_rule_id="bootstrap",
        thesis_id="deterministic-baseline",
        symbol=symbol,
        scope=DecisionScope.SPOT_DAILY,
        parameters=SpotRuleParameters(),
        created_at=NOW,
        model_ref="deterministic/baseline",
        prompt_version="spot-baseline-v1",
    )


def test_asset_rule_registry_is_independent_and_btc_compatible(tmp_path):
    store = IntradayStore(tmp_path / "rules.sqlite")
    btc, eth = _rule("BTCUSDT"), _rule("ETHUSDT")
    assert btc.content_hash != eth.content_hash
    for rule in (btc, eth):
        store.register_scoped_rule(rule, status="champion")
        store.activate_scoped_champion(rule.scope, rule.rule_id, now=NOW, symbol=rule.symbol)

    assert store.load_active_scoped_rule(DecisionScope.SPOT_DAILY).rule_id == btc.rule_id
    assert store.load_active_scoped_rule(DecisionScope.SPOT_DAILY, symbol="ETHUSDT").rule_id == eth.rule_id
    assert store.scoped_rule_registry(DecisionScope.SPOT_DAILY, symbol="ETHUSDT")["champion_id"] == eth.rule_id
    with sqlite3.connect(store.database) as connection:
        old = connection.execute("SELECT champion_id FROM scoped_rule_registry WHERE scope='spot_daily'").fetchone()
    assert old == (btc.rule_id,)
    assert store.schema_version() == 22


def test_schema_v20_rule_registry_backfills_btc(tmp_path):
    database = tmp_path / "rules.sqlite"
    btc = _rule("BTCUSDT")
    with sqlite3.connect(database) as connection:
        connection.executescript("""
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_meta VALUES ('schema_version', '20');
            CREATE TABLE scoped_rules (
                id TEXT PRIMARY KEY, scope TEXT NOT NULL, parent_id TEXT NOT NULL,
                status TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE scoped_rule_registry (
                scope TEXT PRIMARY KEY, champion_id TEXT, challenger_id TEXT,
                rollback_id TEXT, updated_at TEXT NOT NULL
            );
            CREATE TABLE scoped_rule_evaluations (
                id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, scope TEXT NOT NULL,
                kind TEXT NOT NULL, status TEXT NOT NULL, evaluated_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE scoped_rule_soak_ticks (
                id INTEGER PRIMARY KEY AUTOINCREMENT, candidate_id TEXT NOT NULL,
                signal_id INTEGER NOT NULL, champion_allowed INTEGER NOT NULL,
                challenger_allowed INTEGER NOT NULL, champion_score REAL NOT NULL,
                challenger_score REAL NOT NULL, hard_risk_violation INTEGER NOT NULL,
                created_at TEXT NOT NULL, UNIQUE(candidate_id, signal_id)
            );
        """)
        connection.execute(
            "INSERT INTO scoped_rules VALUES (?, ?, ?, 'champion', ?, ?)",
            (btc.rule_id, btc.scope.value, btc.parent_rule_id,
             btc.model_dump_json(exclude={"symbol"}), NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO scoped_rule_registry VALUES (?, ?, NULL, NULL, ?)",
            (btc.scope.value, btc.rule_id, NOW.isoformat()),
        )
    migrated = IntradayStore(database)
    assert migrated.scoped_rule_registry(DecisionScope.SPOT_DAILY)["champion_id"] == btc.rule_id
    assert migrated.load_active_scoped_rule(DecisionScope.SPOT_DAILY).symbol == "BTCUSDT"
    with sqlite3.connect(database) as connection:
        for table in ("scoped_rules", "scoped_rule_evaluations", "scoped_rule_soak_ticks"):
            assert "symbol" in {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
    assert migrated.schema_version() == 22


def test_daily_candles_are_immutable_and_replay_requires_history(tmp_path):
    store = IntradayStore(tmp_path / "rules.sqlite")
    candle = [0, "100", "102", "99", "101", "10", 86_399_999]
    assert store.record_asset_daily_candles("ETHUSDT", [candle]) == 1
    assert store.record_asset_daily_candles("ETHUSDT", [candle]) == 0
    assert store.list_asset_daily_candles("ETHUSDT") == [candle]
    candidate = _rule("ETHUSDT")
    store.register_scoped_rule(candidate)
    evaluation = replay_asset_spot_rule(store, candidate.rule_id, now=NOW)
    assert evaluation.status == "deferred"
    assert "minimum_730_closed_candles" in evaluation.reason_codes
    assert evaluation.symbol == "ETHUSDT"


def _trending_candles(count=760):
    rows = []
    first_open = int((NOW - timedelta(days=count)).timestamp() * 1000)
    for index in range(count):
        cycle, phase = divmod(index, 50)
        close = 100 + cycle * 20 + (phase if phase < 35 else 35 - (phase - 35) * 1.1)
        opening = first_open + index * 86_400_000
        rows.append([opening, str(close - 0.1), str(close + 0.4),
                     str(close - 0.4), str(close), "10", opening + 86_399_999])
    return rows


def test_eth_spot_bootstrap_replay_and_exact_soak_gate(tmp_path):
    class Client:
        def candles(self, *, symbol, limit, now):
            assert (symbol, limit) == ("ETHUSDT", 1000)
            return _trending_candles()

    store = IntradayStore(tmp_path / "rules.sqlite")
    candidate = bootstrap_asset_spot_rule(store, "ETHUSDT", now=NOW, client=Client())
    replay = replay_asset_spot_rule(store, candidate.rule_id, now=NOW)
    assert replay.status == "pass"
    assert replay.metrics["closed_trades"] >= 6
    assert replay.metrics["max_drawdown_pct"] < 8
    with pytest.raises(ValueError, match="exact passing replay"):
        start_asset_spot_soak(store, candidate.rule_id, evaluation_id="wrong", now=NOW)
    assert store.asset_lifecycle("ETHUSDT", DecisionScope.SPOT_DAILY).stage.value == "shadow"

    registry = start_asset_spot_soak(store, candidate.rule_id,
                                     evaluation_id=replay.evaluation_id, now=NOW)
    assert registry["challenger_id"] == candidate.rule_id
    assert store.asset_lifecycle_record("ETHUSDT", DecisionScope.SPOT_DAILY)["evaluation_id"] == replay.evaluation_id
    assert store.load_active_scoped_rule(DecisionScope.SPOT_DAILY, symbol="ETHUSDT") is None
    assert evaluate_asset_spot_soak(store, candidate.rule_id, now=NOW).status == "deferred"


def test_shadow_only_asset_cannot_bootstrap_rule(tmp_path):
    store = IntradayStore(tmp_path / "rules.sqlite")
    with pytest.raises(ValueError, match="ETHUSDT spot_daily only"):
        bootstrap_asset_spot_rule(store, "HYPEUSDT", now=NOW)


def test_llm_asset_proposal_is_operator_triggered_bounded_and_owned(tmp_path):
    class Client:
        model_ref = "test/llm"

        def complete(self, **kwargs):
            assert kwargs["workflow"] == "asset_spot_rule_generator"
            assert kwargs["input_payload"]["symbol"] == "ETHUSDT"
            assert kwargs["input_payload"]["champion"]["symbol"] == "ETHUSDT"
            return SpotRuleProposal(
                parameters=SpotRuleParameters(entry_window=25),
                rationale="Use a slightly longer entry window for ETH daily breakout confirmation.",
            )

    store = IntradayStore(tmp_path / "rules.sqlite")
    champion = _rule("ETHUSDT")
    store.register_scoped_rule(champion, status="champion")
    store.activate_scoped_champion(champion.scope, champion.rule_id, now=NOW, symbol=champion.symbol)
    store.record_snapshot(FeatureSnapshot.create(
        symbol="ETHUSDT", market="binance_spot", timeframe="1d",
        event_time=NOW, built_at=NOW, bid=99, ask=101,
        features={"price": 100}, freshness={"candles": True},
    ))

    candidate = propose_asset_spot_rule(store, "ETHUSDT", client=Client(), now=NOW)

    assert candidate.symbol == "ETHUSDT"
    assert candidate.parent_rule_id == champion.rule_id
    assert store.has_open_scoped_rule_candidate(DecisionScope.SPOT_DAILY, symbol="ETHUSDT")
    assert not store.has_open_scoped_rule_candidate(DecisionScope.SPOT_DAILY)
    with pytest.raises(ValueError, match="open candidate"):
        propose_asset_spot_rule(store, "ETHUSDT", client=Client(), now=NOW)


def test_spot_soak_outcome_matures_from_three_closed_daily_candles(tmp_path):
    store = IntradayStore(tmp_path / "rules.sqlite")
    candidate = _rule("ETHUSDT")
    store.register_scoped_rule(candidate)
    signal_id = store.record_journal_signal(
        decision_id="eth-spot-setup-1", timestamp=NOW, symbol="ETHUSDT",
        scope=DecisionScope.SPOT_DAILY, state_snapshot='{"symbol":"ETHUSDT"}',
        raw_signals={"price": 100.0},
        jev_answers={"direction": {"choice": "Buy", "probabilities": {"Buy": 0.9}}},
        gate_passed=False, gate_reason="asset_soak_observation_only",
        rules_version=candidate.rule_id, llm_thesis=None, market="binance_spot",
    )
    store.record_scoped_rule_soak_tick(
        candidate_id=candidate.rule_id, signal_id=signal_id,
        champion_allowed=False, challenger_allowed=True,
        champion_score=0, challenger_score=0, created_at=NOW,
    )
    first_open = int(NOW.timestamp() * 1000) - 86_400_000
    store.record_asset_daily_candles("ETHUSDT", [
        [first_open + index * 86_400_000, str(100 + index),
         str(101 + index), str(99 + index), str(100 + index), "10",
         first_open + (index + 1) * 86_400_000 - 1]
        for index in range(4)
    ])

    early = store.list_scoped_rule_soak_ticks(candidate.rule_id, as_of=NOW + timedelta(days=2))
    mature = store.list_scoped_rule_soak_ticks(candidate.rule_id, as_of=NOW + timedelta(days=3))

    assert early[0]["directional_return_pct"] is None
    assert mature[0]["directional_return_pct"] == pytest.approx(3.0)


def test_thirty_day_asset_soak_promotes_only_with_exact_pass_id(tmp_path):
    store = IntradayStore(tmp_path / "rules.sqlite")
    candidate = _rule("ETHUSDT")
    store.register_scoped_rule(candidate, status="replay_passed")
    replay = ScopedRuleEvaluation.create(
        candidate_id=candidate.rule_id, symbol="ETHUSDT", scope=DecisionScope.SPOT_DAILY,
        kind="replay", status="pass", evaluated_at=NOW, started_at=None,
        sample_count=8, coverage=1.0, champion_score=0, challenger_score=5,
    )
    store.record_scoped_rule_evaluation(replay)
    start_asset_spot_soak(store, candidate.rule_id, evaluation_id=replay.evaluation_id, now=NOW)
    first_open = int(NOW.timestamp() * 1000) - 86_400_000
    store.record_asset_daily_candles("ETHUSDT", [
        [first_open + index * 86_400_000, str(100 + index),
         str(101 + index), str(99 + index), str(100 + index), "10",
         first_open + (index + 1) * 86_400_000 - 1]
        for index in range(35)
    ])
    for day in range(30):
        at = NOW + timedelta(days=day)
        store.record_portfolio_soak_tick(
            symbol="ETHUSDT", scope=DecisionScope.SPOT_DAILY,
            status="success" if day in {0, 10, 20} else "skipped_no_setup",
            created_at=at,
        )
        if day not in {0, 10, 20}:
            continue
        signal_id = store.record_journal_signal(
            decision_id=f"eth-spot-setup-{day}", timestamp=at, symbol="ETHUSDT",
            scope=DecisionScope.SPOT_DAILY, state_snapshot='{"symbol":"ETHUSDT"}',
            raw_signals={"price": float(100 + day)},
            jev_answers={"direction": {"choice": "Buy", "probabilities": {"Buy": 0.9}}},
            gate_passed=False, gate_reason="asset_soak_observation_only",
            rules_version=candidate.rule_id, llm_thesis=None, market="binance_spot",
        )
        store.record_scoped_rule_soak_tick(
            candidate_id=candidate.rule_id, signal_id=signal_id,
            champion_allowed=False, challenger_allowed=True,
            champion_score=0, challenger_score=0, created_at=at,
        )

    evaluated_at = NOW + timedelta(days=30)
    soak = evaluate_asset_spot_soak(store, candidate.rule_id, now=evaluated_at)
    assert soak.status == "pass"
    assert soak.sample_count == 3
    with pytest.raises(ValueError, match="exact passing soak"):
        activate_asset_spot_rule(store, candidate.rule_id, evaluation_id="wrong", now=evaluated_at)
    registry = activate_asset_spot_rule(
        store, candidate.rule_id, evaluation_id=soak.evaluation_id, now=evaluated_at,
    )
    assert registry["champion_id"] == candidate.rule_id
    assert store.asset_lifecycle_record("ETHUSDT", DecisionScope.SPOT_DAILY)["evaluation_id"] == soak.evaluation_id
    assert store.asset_lifecycle("ETHUSDT", DecisionScope.SPOT_DAILY).stage.value == "soak"
    assert store.load_active_scoped_rule(DecisionScope.SPOT_DAILY) is None
