from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D

from intraday.jev_review import ReviewSignal, build_plan, scan_route
from intraday.jev_review_cache import AnswerCache
from intraday.replay_v2.contracts import FundingHistory, FundingSettlement
from intraday.setup2_review import encode_state
from test_setup2_review import observation, history, payload, NOW
from test_jev_review_overlay import route


def test_scanner_includes_signals_while_rule_only_position_is_open(route):
    config, data, baseline = route
    signals = scan_route(config, data)
    assert len(signals) >= len(baseline['trades'])
    assert all(config.start <= s.bar_close < config.end and s.rule_id == config.rule_id for s in signals)
    assert any(s.bar_close.isoformat() not in {t['entered_at'] for t in baseline['trades']} for s in signals)


def test_plan_deduplicates_states_and_reports_only_missing_calls_without_writes(tmp_path):
    tokens = tuple(encode_state(observation(), history()))
    signals = [ReviewSignal('a', NOW, tokens), ReviewSignal('b', NOW+timedelta(hours=4), tokens)]
    missing_path = tmp_path/'absent'/'cache.sqlite3'
    with AnswerCache(missing_path) as cache:
        plan = build_plan(signals, cache)
        again = build_plan(signals, cache)
    assert plan == again and not missing_path.parent.exists()
    assert plan['entry_bars'] == 2 and plan['unique_states'] == plan['missing_states'] == 1
    assert plan['estimated_input_tokens'] > 0
    assert plan['estimated_cost_usd'] == plan['estimated_input_tokens']*.042/1_000_000
    assert plan['network_calls'] == 0
    with AnswerCache(tmp_path/'cache.sqlite3', writable=True) as cache:
        cache.put(tokens, payload())
    with AnswerCache(tmp_path/'cache.sqlite3') as cache:
        cached = build_plan(signals, cache)
    assert cached['missing_states'] == cached['estimated_input_tokens'] == 0
    assert plan['plan_hash'] != cached['plan_hash']


def test_future_funding_cannot_change_an_earlier_state(route):
    config, data, _ = route
    if config.market != 'perp': return
    funding = FundingHistory(symbol=config.symbol, source='test', coverage_start=config.start-timedelta(days=1),
        coverage_end=config.end+timedelta(days=1), settlements=(
            FundingSettlement(at=config.start-timedelta(hours=8), rate=D('-.001'), mark=100),))
    config = config.model_copy(update={'profile': config.profile.model_copy(update={'funding': funding})})
    before = scan_route(config, data)
    later = replace(data)
    changed = funding.model_copy(update={'settlements': funding.settlements+(
        FundingSettlement(at=config.end, rate=D('.1'), mark=100),)})
    config = config.model_copy(update={'profile': config.profile.model_copy(update={'funding': changed})})
    assert scan_route(config, later) == before
