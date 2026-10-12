from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from test_donchian_wide_data import END, NAMES, START, Exchange, fetchers
from intraday.replay_v2 import donchian_h1_verdict as verdict
from intraday.replay_v2 import donchian_wide_data as wide
from intraday.replay_v2.contracts import Candle
from intraday.replay_v2.donchian_adx_setups import (
    H1SlowRegimeConfig, basket_specification, h1_slowregime_cases)
from intraday.replay_v2.donchian_filter_data import decode_bundle
from intraday.replay_v2.donchian_filters import indicator_series
from intraday.replay_v2.donchian_hourly import build
from intraday.replay_v2.donchian_wide_config import WideSlotConfig, wide_cases


def ramp(count):
    """A noisy ramp so every smoothing length yields different numbers."""
    rows, price = [], D(100)
    for i in range(count):
        price += D(1) if i % 3 else D(-1)
        rows.append(Candle(opened_at=START+timedelta(hours=4*i), available_at=START+timedelta(hours=4*i+4),
                           open=price, high=price+D(1+i % 4), low=price-D(1+i % 5), close=price, volume=D(10+i)))
    return rows


def test_adx_period_changes_only_the_directional_smoothing():
    rows = ramp(260)
    default, explicit, slow = (indicator_series(rows), indicator_series(rows, adx_period=14),
                               indicator_series(rows, adx_period=56))
    assert default == explicit
    assert default[27]['adx'] is not None and default[26]['adx'] is None      # 14 TR + 14 DX
    assert slow[111]['adx'] is not None and slow[110]['adx'] is None          # 56 TR + 56 DX
    assert all(a['atr'] == b['atr'] and a['volume_ma'] == b['volume_ma'] for a, b in zip(default, slow))
    assert default[200]['adx'] != slow[200]['adx']


def test_the_slow_ema_pair_seeds_at_200_and_800_closes():
    rows = ramp(900)
    values = indicator_series(rows, ema_fast=200, ema_slow=800, adx_period=56)
    assert values[198]['ema_fast'] is None and values[199]['ema_fast'] == sum(r.close for r in rows[:200])/200
    assert values[798]['ema_slow'] is None and values[799]['ema_slow'] == sum(r.close for r in rows[:800])/800
    assert values[800]['ema_slow'] == values[799]['ema_slow']+D(2)/801*(rows[800].close-values[799]['ema_slow'])


def test_wide_manifest_records_a_longer_m15_history_and_decode_needs_to_be_told(tmp_path):
    names = NAMES[:6]
    universe = dict(candidates=[dict(symbol=s, rank=i+1, quote_volume_90d=1.0) for i, s in enumerate(names)],
                    excluded=[], as_of=END.isoformat(), method='test')
    path = wide.collect(tmp_path/'wide', universe, START, END, want=6, progress=lambda *_: None,
                        m15_warmup=timedelta(hours=1000), **fetchers(Exchange()))
    raw = wide.read_wide_manifest(path)
    assert raw['m15_warmup_hours'] == 1000
    config = WideSlotConfig(start=START, end=END, symbols=tuple(sorted(names)))
    with pytest.raises(ValueError, match='identity mismatch'):
        decode_bundle(config, raw)
    data, _ = decode_bundle(config, raw, m15_warmup=timedelta(hours=1000))
    symbol = sorted(names)[0]
    assert data[symbol]['spot15m'][0].opened_at == START-timedelta(hours=1000)
    # The long history only feeds the slow H1 series; every other series keeps its fixed warmup.
    hourly, derivation = build(data, [symbol], START, END, warmup_hours=1000)
    assert hourly[symbol]['spot1h'][0].opened_at == START-timedelta(hours=1000)
    assert hourly[symbol]['spot15m'][0].opened_at == START-timedelta(hours=484)
    assert len(hourly[symbol]['spot1h']) == 1000+48 and derivation[symbol]['spot']['synthetic_bars'] == 0
    with pytest.raises(ValueError, match='shorter than the 2000h warmup'):
        build(data, [symbol], START, END, warmup_hours=2000)


def test_slow_regime_settings_are_locked_together_and_the_registered_list_is_unchanged():
    symbols = tuple(f'A{i}XUSDT' for i in range(8))
    slow = dict(bar_interval='1h', ema_fast=200, ema_slow=800, adx_period=56, warmup_hours=1000)
    WideSlotConfig(start=START, end=END, symbols=symbols, **slow)
    for broken in (dict(slow, adx_period=14), dict(slow, warmup_hours=484), dict(slow, bar_interval='4h'),
                   dict(slow, regime_interval='4h')):
        with pytest.raises(ValidationError):
            WideSlotConfig(start=START, end=END, symbols=symbols, **broken)
    with pytest.raises(ValidationError):
        WideSlotConfig(start=START, end=END, symbols=symbols, bar_interval='1h', adx_period=56)
    registered = [n for n, _ in wide_cases(START, END, symbols)]
    assert len(registered) == 14 and not any('SLOWREGIME' in n for n in registered)
    subset = wide_cases(START, END, symbols, only=['WIDE-H4', 'WIDE-H1-SLOWREGIME', 'WIDE-H1-SLOWREGIME-cost2x'])
    assert [n for n, _ in subset] == ['WIDE-H4', 'WIDE-H1-SLOWREGIME', 'WIDE-H1-SLOWREGIME-cost2x']
    with pytest.raises(ValueError, match='unknown wide case'):
        wide_cases(START, END, symbols, only=['WIDE-NOPE'])


def test_three_coin_slowregime_cases_pair_it_with_the_hybrid_and_the_baseline():
    cases = dict(h1_slowregime_cases(datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 10, 2, tzinfo=timezone.utc)))
    assert list(cases) == [f'ETH-NEAR-SOL-{label}{suffix}' for label in ('H4', 'H1', 'H1-REGIME', 'H1-SLOWREGIME')
                           for suffix in ('', '-cost2x')]
    payload = cases['ETH-NEAR-SOL-H1-SLOWREGIME'].model_dump(mode='json')
    assert (payload['ema_fast'], payload['ema_slow'], payload['adx_period'], payload['warmup_hours']) == (200, 800, 56, 1000)
    baseline = cases['ETH-NEAR-SOL-H1'].model_dump(mode='json')
    assert not {'adx_period', 'warmup_hours', 'ema_fast'} & set(baseline)
    assert isinstance(cases['ETH-NEAR-SOL-H1-SLOWREGIME'], H1SlowRegimeConfig)
    assert basket_specification(('ETH', 'NEAR', 'SOL'))['adx_by_market']['spot']['ETHUSDT'] == 20


def test_study_runs_a_subset_with_the_slow_regime_and_reads_it(tmp_path):
    from intraday.replay_v2.donchian_adx_study import run_study
    names = NAMES[:6]
    universe = dict(candidates=[dict(symbol=s, rank=i+1, quote_volume_90d=1.0) for i, s in enumerate(names)],
                    excluded=[], as_of=END.isoformat(), method='test')
    manifest = wide.collect(tmp_path/'wide', universe, START, END, want=6, progress=lambda *_: None,
                            m15_warmup=timedelta(hours=1000), **fetchers(Exchange()))
    only = ['WIDE-H4', 'WIDE-H1-SLOWREGIME', 'WIDE-H1-SLOWREGIME-cost2x']
    receipt = run_study(manifest, tmp_path/'runs', universe='wide-slots', progress=lambda *_: None, only=only)
    assert [r['variant'] for r in receipt['results']] == only
    assert all(r['deterministic_rerun_verified'] for r in receipt['results'])
    derivation = receipt['results'][1]['config']
    assert derivation['adx_period'] == 56 and derivation['warmup_hours'] == 1000
    result = verdict.evaluate_wide(receipt)
    assert set(result['candidates']) == {'H1-SLOWREGIME', 'H4'} and 'H4-random' not in result['candidates']
    assert result['advancing'] == []
