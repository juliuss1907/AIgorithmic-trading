from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from intraday.replay_v2.donchian_filters import indicator_series, volume_profile, entry_filters


def bars(count=620):
    return [SimpleNamespace(open=D(100+i), high=D(102+i), low=D(99+i),
                            close=D(101+i), volume=D(10)) for i in range(count)]


def test_closed_series_ema_atr_and_wilder_adx():
    features = indicator_series(bars())
    row = features[-1]
    assert row['atr'] == D(3)
    assert row['adx'] == D(100)
    assert row['plus_di'] > row['minus_di'] == 0
    assert row['ema50'] > row['ema200']
    assert row['ema50'] > row['prior_ema50']


def test_volume_reference_excludes_trigger_and_prefix_is_causal():
    rows = bars()
    rows[-1].volume = D(1000)
    features = indicator_series(rows)
    assert features[-1]['volume_ma'] == 10
    assert features[:-1] == indicator_series(rows[:-1])


def test_profile_conserves_volume_including_zero_range():
    rows = [SimpleNamespace(low=D(10), high=D(20), volume=D(100)),
            SimpleNamespace(low=D(15), high=D(15), volume=D(50))]
    profile = volume_profile(rows, bins=10)
    assert sum(profile['volumes']) == pytest.approx(150)
    assert profile['poc'] == pytest.approx(15.5)
    assert profile['val'] <= profile['poc'] <= profile['vah']
    assert profile['covered_volume'] >= .7*150


def test_flat_price_zero_volume_and_invalid_profile():
    row = SimpleNamespace(low=D(10), high=D(10), volume=D(100))
    assert volume_profile([row])['vah'] == 10
    row.volume = D(0)
    assert volume_profile([row]) is None
    row.volume = D('NaN')
    with pytest.raises(ValueError):
        volume_profile([row])


def test_uniform_profile_poc_tie_uses_lowest_bin_despite_float_roundoff():
    row = SimpleNamespace(low=D(10), high=D(20), volume=D(100))
    assert volume_profile([row], bins=50)['poc'] == pytest.approx(10.1)


def test_strict_directional_filters_and_all_block_reasons():
    f = dict(close=D(110), ema200=D(100), ema50=D(105), prior_ema50=D(104),
             adx=D(26), prior_adx=D(25), plus_di=D(30), minus_di=D(10),
             volume=D(13), volume_ma=D(10), profile={'vah':109, 'val':90})
    assert entry_filters(f, 1, 4) == []
    assert set(entry_filters(f, -1, 4)) == {'ema200', 'ema50', 'dmi', 'volume_profile'}
    f.update(adx=D(25), volume=D(12))
    assert entry_filters(f, 1, 4) == ['adx', 'volume_ma']


def test_channels_use_only_preceding_candles():
    from intraday.replay_v2.donchian_filters import observation
    rows = bars(40)
    rows[-1].high = D(1000)
    rows[-1].close = D(500)
    obs = observation(rows, 20, 8)
    assert obs['upper'] == rows[-2].high
    assert obs['lower_exit'] == min(r.low for r in rows[-9:-1])
    assert obs['long_entry']
