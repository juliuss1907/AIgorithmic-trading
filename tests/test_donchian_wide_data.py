from datetime import datetime, timedelta, timezone
from functools import partial
from urllib.error import HTTPError

import pytest

from intraday.replay_v2 import donchian_wide_data as wide
from intraday.replay_v2.donchian_filter_data import WARMUP, decode_bundle, fetch_spot_snapshot
from intraday.replay_v2.donchian_filter_engine import prepare, simulate
from intraday.replay_v2.donchian_wide_config import WideSlotConfig
from intraday.replay_v2.funding import fetch_funding_snapshot
from intraday.replay_v2.historical_data import fetch_candle_snapshot

START = datetime(2026, 1, 1, tzinfo=timezone.utc)
END = START+timedelta(days=2)
NOW = END
WIDTH = {'4h': 14_400_000, '15m': 900_000, '1d': 86_400_000}


def row(opened, width, price='100'):
    volume = str(10*width//900_000)  # Quarter-hour volume 10, so H4 candles equal their sixteen quarters.
    return [opened, price, str(float(price)+1), str(float(price)-1), price, volume, opened+width-1, '1000', 5, '5', '500', '0']


class Exchange:
    """Deterministic stand-in for the public endpoints; `broken` maps a symbol to the series that has a gap."""

    def __init__(self, broken=None):
        self.broken = broken or {}

    def _klines(self, symbol, interval, start, end, series):
        width = WIDTH[interval]
        times = list(range(start, end, width))
        if self.broken.get(symbol) == series:
            times.pop(len(times)//2)
        return [row(t, width) for t in times]

    def spot(self, query):
        series = 'spot'+query['interval']
        rows = self._klines(query['symbol'], query['interval'], query['startTime'], query['endTime']+1, series)
        return rows[:query['limit']]

    def futures(self, path, query):
        series = ('mark' if 'mark' in path else 'perp')+query['interval']
        rows = self._klines(query['symbol'], query['interval'], query['startTime'], query['endTime']+1, series)
        return rows[:query['limit']]

    def funding(self, query):
        first = -(-query['startTime']//28_800_000)*28_800_000
        return [dict(symbol=query['symbol'], fundingTime=t, fundingRate='0.0001', markPrice='100')
                for t in range(first, query['endTime']+1, 28_800_000)]


def fetchers(exchange):
    return dict(spot_fetch=partial(fetch_spot_snapshot, fetch_json=exchange.spot, now=NOW),
                perp_fetch=partial(fetch_candle_snapshot, fetch_json=exchange.futures, now=NOW),
                funding_fetch=partial(fetch_funding_snapshot, fetch_json=exchange.funding, now=NOW))


def test_universe_ranks_by_volume_and_reports_every_exclusion():
    warmup = datetime(2025, 9, 22, tzinfo=timezone.utc)
    ms = lambda at: int(at.timestamp()*1000)  # noqa: E731
    symbols = [
        dict(symbol='AAAUSDT', baseAsset='AAA'), dict(symbol='BBBUSDT', baseAsset='BBB'),
        dict(symbol='USDCUSDT', baseAsset='USDC'), dict(symbol='1000PEPEUSDT', baseAsset='1000PEPE'),
        dict(symbol='NEWUSDT', baseAsset='NEW', onboardDate=ms(warmup)+1),
        dict(symbol='GOLDUSDT', baseAsset='GOLD', underlyingType='COMMODITY'),
        dict(symbol='NOSPOTUSDT', baseAsset='NOSPOT'), dict(symbol='LATEUSDT', baseAsset='LATE'),
        dict(symbol='OLDUSDT', baseAsset='OLD', status='SETTLING')]
    for item in symbols:
        item.setdefault('contractType', 'PERPETUAL'); item.setdefault('status', 'TRADING')
        item.setdefault('quoteAsset', 'USDT'); item.setdefault('underlyingType', 'COIN')
        item.setdefault('onboardDate', ms(warmup)-10)
    volumes = {'AAAUSDT': 1, 'BBBUSDT': 3, 'NOSPOTUSDT': 9, 'LATEUSDT': 5, 'USDCUSDT': 100}

    def get(url, query=None):
        if url == wide.FUT_INFO:
            return {'symbols': symbols}
        if url == wide.FUT_TICKER:
            return [dict(symbol=s, quoteVolume=str(v)) for s, v in volumes.items()]
        if url == wide.FUT_KLINES:
            return [[0, 0, 0, 0, 0, 0, 0, str(volumes[query['symbol']])] for _ in range(90)]
        if url == wide.SPOT_INFO:
            if query['symbol'] == 'NOSPOTUSDT':
                raise HTTPError(url, 400, 'invalid symbol', {}, None)
            return {'symbols': [dict(status='TRADING')]}
        if url == wide.SPOT_KLINES:
            return [[ms(warmup)+1 if query['symbol'] == 'LATEUSDT' else ms(warmup)-1]]
        raise AssertionError(url)

    result = wide.select_universe(get, warmup_start=warmup, now=NOW, preselect=10, keep=5, pace=0)
    assert [c['symbol'] for c in result['candidates']] == ['BBBUSDT', 'AAAUSDT']
    assert [c['rank'] for c in result['candidates']] == [1, 2]
    reasons = {e['symbol']: e['reason'] for e in result['excluded']}
    assert reasons['USDCUSDT'] == 'stablecoin' and reasons['1000PEPEUSDT'] == 'symbol_form_or_multiplier'
    assert reasons['NEWUSDT'] == 'perp_onboarded_after_warmup' and reasons['GOLDUSDT'] == 'not_coin_underlying'
    assert reasons['NOSPOTUSDT'] == 'no_spot_market' and reasons['LATEUSDT'] == 'spot_history_starts_after_warmup'
    assert 'OLDUSDT' not in reasons  # Settling contracts are not reported as noise.


def collect(tmp_path, exchange, names, want):
    universe = dict(candidates=[dict(symbol=s, rank=i+1, quote_volume_90d=1.0) for i, s in enumerate(names)],
                    excluded=[], as_of=NOW.isoformat(), method='test')
    return wide.collect(tmp_path/'wide', universe, START, END, want=want, progress=lambda *_: None, **fetchers(exchange))


NAMES = ['AAAUSDT', 'BBBUSDT', 'CCCUSDT', 'DDDUSDT', 'EEEUSDT', 'FFFUSDT', 'GGGUSDT']


def test_collect_drops_a_coin_with_a_gap_and_takes_the_next_ranked_one(tmp_path):
    exchange = Exchange(broken={'CCCUSDT': 'spot15m'})
    path = collect(tmp_path, exchange, NAMES, want=6)
    raw = wide.read_wide_manifest(path)
    assert sorted(raw['candles']) == ['AAAUSDT', 'BBBUSDT', 'DDDUSDT', 'EEEUSDT', 'FFFUSDT', 'GGGUSDT']
    manifest = wide.read_inputs(path)
    assert [r['symbol'] for r in manifest['rejected']] == ['CCCUSDT']
    assert manifest['rank_order'][:3] == ['AAAUSDT', 'BBBUSDT', 'DDDUSDT']
    assert set(raw['candles']['AAAUSDT']) == set(wide.TAGS) and 'AAAUSDT' in raw['funding']


def test_collect_reports_failure_when_too_few_coins_validate(tmp_path):
    broken = {s: 'perp4h' for s in NAMES[:3]}
    with pytest.raises(ValueError, match='only 4 of 6'):
        collect(tmp_path, Exchange(broken=broken), NAMES, want=6)


def test_manifest_round_trips_through_decode_bundle_and_the_engine(tmp_path):
    path = collect(tmp_path, Exchange(), NAMES, want=6)
    raw = wide.read_wide_manifest(path)
    symbols = tuple(sorted(raw['candles']))
    config = WideSlotConfig(start=START, end=END, symbols=symbols)
    data, funding = decode_bundle(config, raw)
    assert all(len(data[s]['spot15m']) == len(data[s]['perp15m']) for s in symbols)
    assert data[symbols[0]]['spot4h'][0].opened_at == START-WARMUP['4h']
    report = simulate(config, prepare(config, data, funding))
    assert report['summary']['closed_trades'] == 0 and report['status'] == 'complete'
    # Tampering with a stored snapshot is caught before any replay.
    victim = next(iter(wide.read_inputs(path)['files']['AAAUSDT'].values()))['path']
    with open(victim, 'a') as stream:
        stream.write(' ')
    with pytest.raises(ValueError, match='changed'):
        wide.read_wide_manifest(path)


def test_study_runs_the_registered_fourteen_cases_and_the_wide_verdict_reads_them(tmp_path):
    from intraday.replay_v2 import donchian_h1_verdict as verdict
    from intraday.replay_v2.donchian_adx_study import run_study
    manifest = collect(tmp_path, Exchange(), NAMES, want=6)
    receipt = run_study(manifest, tmp_path/'runs', universe='wide-slots', progress=lambda *_: None)
    assert receipt['preset'] == 'donchian-wide-slots' and len(receipt['results']) == 14
    assert all(item['deterministic_rerun_verified'] for item in receipt['results'])
    assert [r['variant'] for r in receipt['results']][:2] == ['WIDE-H4', 'WIDE-H4-cost2x']
    result = verdict.evaluate_wide(receipt)
    assert set(result['candidates']) == {'H1', 'H1-GEOM', 'H1-ADX30', 'H1-EMA50-20', 'H1-REGIME', 'H4', 'H4-random'}
    assert result['advancing'] == []  # Flat synthetic prices earn nothing.
    assert result['candidates']['H1']['slot_profile']['average_open_positions'] == 0
    assert (tmp_path/'runs'/'comparison.md').read_text().count('WIDE-') >= 14
