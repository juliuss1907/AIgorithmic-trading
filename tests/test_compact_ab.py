import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from intraday.contracts import (DecisionMode, DecisionScope, FeatureSnapshot, ProviderKind, ProviderProfile,
                                ProviderRole, StateVariant)
from intraday.outcomes import SignalOutcome
from intraday.provider_client import HttpResponse
from intraday.provider_profiles import ProviderCredential
from intraday.providers import JevDecisionProvider
from intraday.replay_v2 import compact_ab
from intraday.replay_v2.compact_ab import BudgetExceeded, Candidate, select_samples
from intraday.store import IntradayStore

START = datetime(2026, 9, 26, tzinfo=timezone.utc)
SCOPE = DecisionScope.PERP_INTRADAY
DIRECTIONS = {'long': 'Buy', 'short': 'Sell', 'hold': 'Hold'}
SYMBOLS = ('BTCUSDT', 'ETHUSDT')


def pattern(i):
    return 'long' if i % 5 == 0 else 'short' if i % 7 == 0 else 'hold'


def build_source(tmp_path, ticks=96, tampered=()):
    """Two coins, one decision every 10 minutes, outcomes that mostly confirm the stored direction."""
    store = IntradayStore(tmp_path/'source.sqlite')
    for symbol in SYMBOLS:
        for i in range(ticks):
            at = START+timedelta(minutes=10*i)
            snapshot = FeatureSnapshot.create(
                symbol=symbol, market='binance_usdm_perp', timeframe='1h', feature_schema_version='2',
                event_time=at, built_at=at, bid=99.99, ask=100.01,
                features={'reference_price': 100+i, 'mark_price': 100+i, 'bb_mid': 100, 'bb_upper': 104, 'bb_lower': 96,
                          'macd': .5, 'macd_signal': .2, 'macd_hist': .3, 'rsi14': 40+i % 30, 'spread_bps': 1.5},
                freshness={'candles': True, 'order_book': True})
            store.record_snapshot(snapshot)
            side = pattern(i)
            state = json.loads(json.dumps(JevDecisionProvider._state(snapshot, SCOPE)))
            if i in tampered:
                state['bid'] = 50  # Stored state no longer matches the immutable snapshot.
            signal_id = store.record_journal_signal(
                decision_id=f'{symbol}-{i}', timestamp=at, symbol=symbol, scope=SCOPE,
                state_snapshot=json.dumps(state, sort_keys=True, separators=(',', ':')),
                raw_signals={'price': 100.0},
                jev_answers={'direction': {'choice': DIRECTIONS[side], 'probabilities': {DIRECTIONS[side]: .9}}},
                gate_passed=False, gate_reason='test', rules_version='perp-v1', llm_thesis=None,
                feature_schema_version='2', state_variant=StateVariant.NUMERIC_V1, decision_mode=DecisionMode.PRIMARY, experiment_pair_id=None)
            move = {'long': 0.5, 'short': -0.5, 'hold': 0.0}[side] if i % 4 else 0.0
            store.record_signal_outcome(SignalOutcome(
                outcome_id=f'outcome-{signal_id:016d}', signal_id=signal_id, horizon_sec=900,
                observed_at=at+timedelta(minutes=15), entry_price=100, exit_price=100*(1+move/100),
                forward_return_pct=move, max_upside_pct=max(0, move), max_downside_pct=min(0, move),
                directional_return_pct=move, mfe_pct=max(0, move), mae_pct=min(0, move), sample_count=31,
                coverage_pct=100))
    return tmp_path/'source.sqlite'


def make_provider(compact_direction=lambda i: None):
    """Real JevDecisionProvider with a fake transport; answers repeat the stored pattern."""
    def factory(scratch):
        profile = ProviderProfile.create(
            profile_id='jev-test', role=ProviderRole.JEV, kind=ProviderKind.OPENROUTER_DECISIONS,
            base_url='https://openrouter.ai/api/alpha/decisions', model='typesafe/jev-1.13', credential_version='v1',
            created_at=START, updated_at=START)

        last = {'index': 0}

        def transport(**kwargs):
            state = json.loads(kwargs['body'])['state']
            compact = 'tokens' in state
            if not compact:  # The harness asks numeric first, then compact, for the same sample.
                last['index'] = int(state['features']['reference_price']-100)
            index = last['index']
            side = pattern(index)
            override = compact_direction(index) if compact else None
            choice = DIRECTIONS[override or side]
            answers = {'direction': {'choice': choice, 'probabilities': {choice: .9}},
                       'regime': {'choice': 'Sideways'}, 'risk_level': {'choice': 'Low'},
                       'entry_quality': {'score': 3}, 'toxic_flow': {'noul': .1}}
            usage = {'prompt_tokens': 60 if compact else 1400, 'completion_tokens': 20, 'cost': 0.00001 if compact else 0.00006}
            return HttpResponse(200, {}, json.dumps({'answers': answers, 'usage': usage}).encode())
        return JevDecisionProvider(ProviderCredential(profile, 'fixture-only'), store=scratch, transport=transport)
    return factory


def candidates(specs):
    return [Candidate(i, symbol, START+timedelta(minutes=minutes), stratum, 0.0)
            for i, (symbol, minutes, stratum) in enumerate(specs, 1)]


def test_selection_is_deterministic_spaced_and_prefers_actionable_buckets():
    specs = [('BTCUSDT', m, 'hold') for m in range(0, 120, 1)]
    specs += [('BTCUSDT', 45, 'long'), ('BTCUSDT', 91, 'short'), ('ETHUSDT', 5, 'hold')]
    chosen, mix = select_samples(candidates(specs), per_coin=4, spacing=timedelta(minutes=30), seed='s')
    again, _ = select_samples(candidates(specs), per_coin=4, spacing=timedelta(minutes=30), seed='s')
    assert [c.signal_id for c in chosen] == [c.signal_id for c in again]
    btc = [c for c in chosen if c.symbol == 'BTCUSDT']
    assert len(btc) == 4 and {c.stratum for c in btc} >= {'long', 'short'}
    assert len({int(c.at.timestamp())//1800 for c in btc}) == len(btc)  # one per 30 minute bucket
    assert sum(1 for c in chosen if c.symbol == 'ETHUSDT') == 1
    assert sum(mix.values()) == pytest.approx(1)


def test_dry_run_reports_the_plan_and_touches_nothing(tmp_path):
    path = build_source(tmp_path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    plan = compact_ab.run(path, tmp_path/'report', dry_run=True, per_coin=20)
    assert plan['status'] == 'dry_run' and plan['samples'] == 40 and set(plan['per_coin']) == set(SYMBOLS)
    assert plan['estimate']['calls'] == 80
    assert not (tmp_path/'report').exists()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_run_scores_pairs_against_outcomes_and_never_writes_the_source(tmp_path):
    path = build_source(tmp_path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    summary = compact_ab.run(path, tmp_path/'report', provider_factory=make_provider(), per_coin=30, rounds=200,
                             min_pairs=50)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert summary['samples'] == 60 and summary['metrics']['paired_samples'] == 60
    metrics = summary['metrics']
    assert metrics['direction_agreement'] == 1 and metrics['actionable_flip_rate'] == 0
    assert metrics['numeric_accuracy'] == metrics['compact_accuracy'] == metrics['stored_accuracy']
    assert metrics['numeric_repeat_agreement'] == 1
    assert summary['telemetry']['token_reduction'] > .9 and summary['telemetry']['compact_availability'] == 1
    assert summary['status'] == 'eligible' and 'minimum_14_days' in summary['waived_offline']
    assert metrics['confirmations_at_0_85']['compact_long']['confirmed'] > 0
    for name in ('summary.json', 'pairs.jsonl', 'report.md', 'scratch.sqlite'):
        assert (tmp_path/'report'/name).is_file()
    assert (tmp_path/'report'/'report.md').read_text().startswith('# A/B numeric vs compact Jev (offline) — eligible')
    with sqlite3.connect(tmp_path/'report'/'scratch.sqlite') as c:
        workflows = {r[0] for r in c.execute("SELECT json_extract(payload_json,'$.workflow') FROM model_calls")}
    assert workflows == {'perp_intraday_entry', 'perp_intraday_entry_compact'}


def test_a_worse_compact_state_is_rejected_with_the_stock_criteria(tmp_path):
    path = build_source(tmp_path)
    summary = compact_ab.run(path, tmp_path/'report', per_coin=30, rounds=200, min_pairs=50,
                             provider_factory=make_provider(lambda i: 'long' if pattern(i) != 'long' else 'short'))
    assert summary['status'] == 'reject'
    assert 'accuracy_regression_above_2pp' in summary['reason_codes']
    assert summary['metrics']['actionable_flip_rate'] > 0


def test_budget_limits_stop_the_run_without_a_verdict(tmp_path):
    path = build_source(tmp_path)
    with pytest.raises(BudgetExceeded, match='max_calls'):
        compact_ab.run(path, tmp_path/'report', provider_factory=make_provider(), per_coin=30, max_calls=10)
    assert not (tmp_path/'report'/'summary.json').exists()


def test_snapshots_that_no_longer_match_the_stored_state_are_skipped(tmp_path):
    path = build_source(tmp_path, tampered=set(range(0, 96, 2)))
    summary = compact_ab.run(path, tmp_path/'report', provider_factory=make_provider(), per_coin=30, rounds=100)
    assert summary['skipped_unverifiable'] > 0
    assert summary['metrics']['paired_samples'] == summary['samples']-summary['skipped_unverifiable']


def test_fewer_pairs_than_the_stock_minimum_is_inconclusive_not_eligible(tmp_path):
    path = build_source(tmp_path)
    summary = compact_ab.run(path, tmp_path/'report', provider_factory=make_provider(), per_coin=30, rounds=100)
    assert summary['status'] == 'inconclusive' and summary['reason_codes'] == ['minimum_pairs_not_met']
    assert summary['min_pairs'] == 1000
