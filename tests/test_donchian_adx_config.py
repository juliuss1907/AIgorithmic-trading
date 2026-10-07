from decimal import Decimal as D

import pytest

from intraday.replay_v2.donchian_filter_book import FilterConfig
from intraday.replay_v2.donchian_filters import entry_filters


def features():
    return dict(close=D(110), ema200=D(100), ema50=D(105), prior_ema50=D(104),
                adx=D(21), prior_adx=D(20), plus_di=D(30), minus_di=D(10),
                volume=D(13), volume_ma=D(10), profile={'vah':109, 'val':90})


@pytest.mark.parametrize('threshold', [15, 18, 20, 25])
def test_threshold_is_strict_and_still_requires_rising_adx(threshold):
    f = features()
    f.update(adx=D(threshold), prior_adx=D(threshold)-1)
    assert entry_filters(f, 1, 4, adx_threshold=threshold) == ['adx']
    f.update(adx=D(threshold)+1, prior_adx=D(threshold))
    assert entry_filters(f, 1, 4, adx_threshold=threshold) == []
    f['prior_adx'] = f['adx']
    assert entry_filters(f, 1, 4, adx_threshold=threshold) == ['adx']


def test_disabling_adx_dmi_retains_ema_volume_and_profile():
    f = features()
    f.update(adx=None, prior_adx=None, plus_di=None, minus_di=None)
    assert entry_filters(f, 1, 4, adx_threshold=None) == []
    f.update(ema200=D(120), volume=D(12), profile={'vah':111, 'val':90})
    assert entry_filters(f, 1, 4, adx_threshold=None) == ['ema200', 'volume_ma', 'volume_profile']


def test_enabled_threshold_keeps_dmi_direction():
    f = features()
    f.update(plus_di=D(10), minus_di=D(30))
    assert entry_filters(f, 1, 4, adx_threshold=20) == ['dmi']


def test_legacy_default_and_serialization_are_unchanged():
    from intraday.replay_v2.donchian_adx_config import START, END
    f = features()
    assert entry_filters(f, 1, 4) == entry_filters(f, 1, 4, adx_threshold=25)
    assert 'adx_threshold' not in FilterConfig(start=START, end=END).model_dump()


def test_five_locked_a4_cases():
    from intraday.replay_v2.donchian_adx_config import cases, START, END
    rows = cases()
    assert [c.adx_threshold for _, c in rows] == [None, 20, 18, 15, 25]
    assert all(c.filter_level == 4 and c.entry_window == 30 and c.exit_window == 10 for _, c in rows)
    assert all(c.start == START and c.end == END for _, c in rows)


@pytest.mark.parametrize('threshold', [0, 14, 19, 26, float('nan'), float('inf'), True])
def test_research_config_rejects_unlocked_thresholds(threshold):
    from intraday.replay_v2.donchian_adx_config import ADXStudyConfig, START, END
    with pytest.raises(ValueError, match='adx_threshold'):
        ADXStudyConfig(start=START, end=END, adx_threshold=threshold)
