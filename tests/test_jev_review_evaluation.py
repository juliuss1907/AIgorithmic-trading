from dataclasses import replace
from decimal import Decimal

import pytest

from intraday import setup2
from intraday.jev_review_evaluation import (GRID, SPLIT, aggregate, bootstrap_difference,
    calibration, choose, period_config, prepared_book)
from intraday.jev_review import ReviewSignal, scan_route
from intraday.jev_review_cache import AnswerCache
from intraday.jev_review_overlay import ReviewOverlay
from test_jev_review_overlay import route
from test_jev_review_provider import TOKENS
from test_setup2_review import payload


def test_split_and_grid_are_frozen_and_tie_break_is_conservative(route):
    config, _, _ = route
    assert len(GRID) == 16
    assert period_config(config, 'tuning').end <= SPLIT
    assert period_config(config, 'test').start >= SPLIT
    a = dict(thresholds=dict(t_skip='.6',t_exh='.7',t_reduce='.6',t_dis='.6'),net_pnl='10')
    b = dict(thresholds=dict(t_skip='.7',t_exh='.7',t_reduce='.6',t_dis='.6'),net_pnl='10')
    assert choose([a,b]) is b


def test_prepared_scores_use_exact_native_trades_and_pnl(route):
    config, data, baseline = route
    prepared = setup2.series_observations(list(data.candles),list(data.profile_candles),
        side=1 if config.market=='spot' else -1,anchor=data.indicator_anchor,
        profile=data.rule.parameters.entry_profile,warmup_bars=data.warmup_bars or setup2.WARMUP_BARS,
        own_from=data.price_proxy_until)
    book = prepared_book(config,data,prepared)
    assert book.trades == baseline['trades']
    assert float(book.equity(book.last_mark)-config.capital) == baseline['summary']['pnl_after_known_costs']
    assert scan_route(config,data,prepared=prepared) == scan_route(config,data)
    veto = ReviewOverlay(tuple((s.rule_id,s.bar_close,'VETO') for s in scan_route(config,data,prepared=prepared)), 'a'*64)
    assert not prepared_book(config,replace(data,jev_review=veto),prepared).trades


def report(profits):
    return dict(config={'capital':'1000'},summary={'net_pnl':sum(profits)}, inputs={'rule_id':'r'},
        equity_curve=[{'at':'a','equity_known':'900'},{'at':'b','equity_known':'1100'}],
        trades=[dict(entered_at=f'2025-12-{i+1:02}T00:00:00+00:00',net_known_pnl=p) for i,p in enumerate(profits)])


def test_aggregate_and_bootstrap_include_trade_count_change_and_zero_crossing():
    base, other = [report([-10,10])], [report([-5,5,0])]
    stats = aggregate(base)
    assert stats['max_drawdown_pct'] == 10 and stats['mean_trade_profit'] == 0
    assert stats['winrate'] == .5
    out = bootstrap_difference(base,other,draws=1000)
    assert out == bootstrap_difference(base,other,draws=1000)
    assert out['difference'] == 0 and out['contains_zero']
    assert bootstrap_difference(base,[report([])])['ci95'] is None


def test_calibration_uses_frozen_tuning_rate_and_fixed_bins(tmp_path):
    from datetime import datetime
    r = report([-10,10])
    signals = [ReviewSignal('r',datetime.fromisoformat(t['entered_at']),TOKENS) for t in r['trades']]
    with AnswerCache(tmp_path/'answers.sqlite3',writable=True) as cache:
        cache.put(TOKENS,payload())
        cal = calibration([r],signals,cache,base_rate=.8)
        assert cal['samples'] == 2 and cal['loss_rate'] == .5
        assert cal['base_rate_forecast'] == .8
        assert cal['base_rate_brier'] == pytest.approx(.34)
        assert cal['questions']['skip']['brier'] == pytest.approx(.41)
        assert cal['questions']['exhaustion']['bins'][1]['samples'] == 2  # Exact .2 -> [.2,.4).
