"""Setup-2 slot profile (ADR-007): Donchian 25/10, 5% stop cap, research parity on the same bars."""
from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from intraday import setup2
from intraday.contracts import (SETUP2_DONCHIAN, PerpRuleParameters, SpotRuleParameters, is_setup2_profile)
from intraday.replay_v2.donchian_filter_book import ATRTrail
from intraday.replay_v2.donchian_filters import entry_filters
from test_setup2_parity import FIRST, live_bars, study, walk  # noqa: F401  (study is a fixture)

PROFILE = 'setup2_slots_v1'


def test_profile_table_and_contract_locks():
    assert SETUP2_DONCHIAN == {'setup2_v1': (30, 10, 14), 'setup2_slots_v1': (25, 10, 14)}
    assert is_setup2_profile('setup2_v1') and is_setup2_profile(PROFILE)
    assert not is_setup2_profile('donchian_v1') and not is_setup2_profile(None)
    assert SpotRuleParameters(entry_profile=PROFILE, entry_window=25, exit_window=10).entry_profile == PROFILE
    with pytest.raises(ValidationError):
        SpotRuleParameters(entry_profile=PROFILE, entry_window=30, exit_window=10)
    with pytest.raises(ValidationError):
        SpotRuleParameters(entry_profile='setup2_v1', entry_window=25, exit_window=10)
    assert PerpRuleParameters(entry_profile=PROFILE).entry_profile == PROFILE
    # Default (legacy) profiles keep serializing without the profile key, so old hashes stay identical.
    assert 'entry_profile' not in SpotRuleParameters().model_dump(mode='json')
    assert 'entry_profile' not in PerpRuleParameters().model_dump(mode='json')


def test_slot_profile_signal_equals_research_25_10_on_the_same_bars(study):
    cfg, data, prepared, _ = study
    checked = differs = 0
    for symbol in cfg.weights:
        for market, side in (('spot', 1), ('perp', -1)):
            h4, m15 = live_bars(data[symbol][market+'4h'], setup2.H4), live_bars(data[symbol][market+'15m'], setup2.M15)
            for at, f in prepared.features[market][symbol].items():
                kw = dict(side=side, anchor=FIRST)
                live = setup2.evaluate_setup2([b for b in h4 if b.available_at <= at],
                                              [b for b in m15 if b.available_at <= at], profile=PROFILE, **kw)
                base = setup2.evaluate_setup2([b for b in h4 if b.available_at <= at],
                                              [b for b in m15 if b.available_at <= at], **kw)
                obs = prepared.observations[market][symbol][at][(25, 10)]
                assert live.bar_close == at and not live.blockers
                assert live.donchian_entry == obs['long_entry' if side > 0 else 'short_entry']
                assert live.exit == obs['long_exit' if side > 0 else 'short_exit']
                assert list(live.failed_filters) == entry_filters(f, side, 4, adx_threshold=20)
                assert live.features == base.features and live.evaluator_version == 'setup2-slots-live-v1'
                assert base.evaluator_version == setup2.EVALUATOR_VERSION
                differs += live.donchian_entry != base.donchian_entry or live.exit != base.exit
                checked += 1
    assert checked == 3*2*12*6 and differs > 0   # The window really changes some decisions.


def test_slot_profile_caps_the_initial_stop_at_five_percent_like_the_research_trail():
    assert setup2.stop_cap(PROFILE) == D('.05') and setup2.stop_cap('setup2_v1') == D('.10')
    for side in (1, -1):
        entry, atr = D('100'), D('4')            # 3*ATR = 12% away: the 5% cap must bind
        research = ATRTrail(side, entry, atr, hard_pct=D('.05')).stop
        live = setup2.trail_stop(side, entry, atr, [], entered_at=FIRST, anchor=FIRST, cap=setup2.stop_cap(PROFILE))
        assert live == research == entry*(1-side*D('.05'))
        tight = setup2.trail_stop(side, entry, D('1'), [], entered_at=FIRST, anchor=FIRST, cap=setup2.stop_cap(PROFILE))
        assert tight == entry-side*3                 # 3% ATR stop is tighter than the cap


def test_unknown_profile_is_refused():
    h4, m15 = walk(7)
    with pytest.raises(ValueError):
        setup2.evaluate_setup2(live_bars(h4, setup2.H4), live_bars(m15, setup2.M15), side=1, anchor=FIRST, profile='x')
