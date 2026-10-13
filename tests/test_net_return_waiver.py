"""Operator exception for one route's net-profit gate criterion (ADR-007 addendum), with a floor."""
import argparse
import copy
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from intraday.active_set import CoinPlan
from intraday.active_set_cli import build_plan
from intraday.replay_v2.artifacts import read_report
from intraday.replay_v2.gates import NET_RETURN_WAIVER_FLOOR_PCT, GateEvidence, evaluate_report_gate
from intraday.replay_v2.lifecycle import replay_gate
from intraday.setup2_cli import candidate_rule
from test_setup2_parity import FIRST
from test_spot_proxy import PROFILE, SYMBOL, build, series  # noqa: F401  (series is a fixture)


def test_the_waiver_names_enabled_markets_and_legacy_payloads_stay_unchanged():
    base = dict(mode='both', spot_weight=D(1), perp_weight=D(1), indicator_anchor=FIRST)
    assert 'waive_net_return' not in CoinPlan(**base).model_dump(mode='json')
    kept = CoinPlan(**base, waive_net_return=('perp',))
    assert CoinPlan.model_validate_json(kept.model_dump_json()) == kept
    for bad in (dict(mode='spot', perp_weight=D(0), waive_net_return=('perp',)), dict(waive_net_return=('perp', 'perp'))):
        with pytest.raises(ValidationError):
            CoinPlan(**{**base, **bad})


def test_cli_records_the_exception_per_route():
    now = datetime(2026, 10, 14, tzinfo=timezone.utc)
    arguments = argparse.Namespace(coins=None, mode='both', coin=['ETH:both', 'ZEC:both', 'HYPE:both'], split=None,
                                   profile=PROFILE, watch=[], anchor=['2024-01-01', 'HYPE=2025-06-01'], warmup=[],
                                   spot_proxy=[], waive_min_trades=[],
                                   waive_net_return=['ZEC:perp', 'HYPE:perp'])
    plan = build_plan(arguments, None, now)
    assert plan.coins['ZECUSDT'].waive_net_return == ('perp',) == plan.coins['HYPEUSDT'].waive_net_return
    assert plan.coins['ETHUSDT'].waive_net_return == ()
    arguments.waive_net_return = ['ZEC:margin']
    with pytest.raises(ValueError, match='COIN:spot or COIN:perp'):
        build_plan(arguments, None, now)


def test_the_gate_waives_only_the_net_criterion_and_only_above_the_floor(tmp_path, series):
    store, _ = build(tmp_path, series)
    now = series[0][-1].available_at+timedelta(minutes=1)
    rule = candidate_rule(store, SYMBOL, 'spot', actor='julius', reason='Setup-2 slot candidate for HYPE', now=now,
                          profile=PROFILE)
    ran = replay_gate(store, rule.rule_id, now=now, report_dir=tmp_path/'reports')
    report = read_report(tmp_path/'reports', ran.run_id)
    waived = GateEvidence(history_bars=672, required_bars=400, history_coverage=1, latest_candle_age_seconds=60,
                          net_return_waived=True)
    strict = waived.model_copy(update={'net_return_waived': False})

    def judged(evidence, **summary):
        edited = copy.deepcopy(report)
        edited['summary'].update(summary)
        return evaluate_report_gate(edited, evidence)
    losing = dict(closed_trades=31, net_return_pct=-1.69, max_drawdown_known_pct=5.7)
    outcome = judged(waived, **losing)
    assert outcome.status == 'pass' and outcome.metrics['waived_nonpositive_net_return'] == 1
    assert outcome.metrics['net_return_waived'] == 1
    assert judged(strict, **losing).reason_codes == ('nonpositive_net_return',)
    assert NET_RETURN_WAIVER_FLOOR_PCT == -5.0
    assert judged(waived, closed_trades=31, net_return_pct=-5.0, max_drawdown_known_pct=5.7).reason_codes == (
        'nonpositive_net_return',)
    assert judged(waived, closed_trades=31, net_return_pct=-1.0, max_drawdown_known_pct=15.0).reason_codes == (
        'max_drawdown_at_least_15pct',)
    assert judged(waived, closed_trades=3, net_return_pct=-1.0, max_drawdown_known_pct=2.0).reason_codes == (
        'minimum_6_closed_trades',)
