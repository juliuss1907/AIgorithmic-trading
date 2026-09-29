from datetime import datetime, timedelta, timezone

from intraday.asset_auto_proposals import auto_propose_asset_rule
from intraday.contracts import (
    DecisionScope, FeatureSnapshot, ScopedRuleCandidate,
    SpotRuleParameters, SpotRuleProposal,
)
from intraday.scoped_rule_lifecycle import ScopedRuleEvaluation
from intraday.store import IntradayStore


NOW = datetime(2026, 9, 29, 0, 5, tzinfo=timezone.utc)
WIDTH = 14_400_000


def test_rejected_bootstrap_can_get_one_bounded_auto_proposal_after_fresh_bars(tmp_path):
    store = IntradayStore(tmp_path / "rules.sqlite")
    prior = ScopedRuleCandidate.create(
        rule_id="eth-spot-4h-baseline-v1", parent_rule_id="bootstrap",
        thesis_id="baseline", symbol="ETHUSDT", scope=DecisionScope.SPOT_4H,
        parameters=SpotRuleParameters(), created_at=NOW - timedelta(days=2),
        model_ref="deterministic/baseline", prompt_version="baseline-v1",
    )
    store.register_scoped_rule(prior, status="rejected")
    store.record_scoped_rule_evaluation(ScopedRuleEvaluation.create(
        candidate_id=prior.rule_id, symbol="ETHUSDT", scope=DecisionScope.SPOT_4H,
        kind="replay", status="reject", evaluated_at=NOW - timedelta(days=1),
        sample_count=8, coverage=1, champion_score=0, challenger_score=-1,
        reason_codes=("nonpositive_net_return",),
    ))
    last_open = int(NOW.timestamp() * 1000) // WIDTH * WIDTH - WIDTH
    store.record_asset_candles("ETHUSDT", "4h", [
        [last_open - (index * WIDTH), "100", "102", "99", "101", "10",
         last_open - index * WIDTH + WIDTH - 1]
        for index in range(4)
    ])
    store.record_snapshot(FeatureSnapshot.create(
        symbol="ETHUSDT", market="binance_spot", timeframe="4h",
        feature_schema_version="3", event_time=NOW, built_at=NOW,
        bid=100, ask=101, features={"price": 100},
        freshness={"candles_4h": True, "candles_8h": True,
                   "candles_1d": True, "order_book": True},
    ))

    class Client:
        model_ref = "test/llm"
        calls = 0

        def complete(self, **kwargs):
            self.calls += 1
            assert kwargs["workflow"] == "asset_spot_4h_rule_generator_ETHUSDT"
            return SpotRuleProposal(
                parameters=SpotRuleParameters(entry_window=25),
                rationale="Use a longer 4h entry channel after failed baseline replay.",
            )

    client = Client()
    candidate = auto_propose_asset_rule(
        store, "ETHUSDT", DecisionScope.SPOT_4H, client=client, now=NOW,
    )
    assert candidate.parent_rule_id == "bootstrap"
    assert candidate.parameters.entry_window == 25
    assert client.calls == 1
    assert auto_propose_asset_rule(
        store, "ETHUSDT", DecisionScope.SPOT_4H, client=client, now=NOW,
    ) is None
    assert client.calls == 1
