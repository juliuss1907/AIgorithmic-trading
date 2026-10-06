import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from intraday.replay_v2 import donchian_prospective as dp
from intraday.replay_v2.donchian_filter_book import FilterConfig
from intraday.replay_v2.donchian_filter_data import SOURCES, fetch_spot_snapshot, snapshot
from intraday.replay_v2.funding import fetch_funding_snapshot
from intraday.replay_v2.historical_data import fetch_candle_snapshot
from intraday.replay_v2.metrics import encoded, fingerprint

FREEZE = dp.FREEZE
NOW = FREEZE+timedelta(days=1)
WIDTH = {'4h': 14_400_000, '15m': 900_000}


def rows(interval, start, end, price='100'):
    w, lo, hi = WIDTH[interval], int(start.timestamp()*1000), int(end.timestamp()*1000)
    return [[t, price, str(int(price)+1), str(int(price)-1), price, '10', t+w-1] for t in range(lo, hi, w)]


def frozen_bundle(tmp_path, end=FREEZE):
    raw = dict(schema_version='donchian-filters-1', window={'start': (end-timedelta(days=1)).isoformat(),
               'end': end.isoformat()}, source_files_sha256={}, candles={}, funding={}, reuse_lineage={})
    for s in ('BTCUSDT', 'ETHUSDT', 'SOLUSDT'):
        raw['candles'][s] = {}
        for tag in dp.TAGS:
            interval = '4h' if tag.endswith('4h') else '15m'
            market = 'spot' if tag.startswith('spot') else 'mark' if tag == 'mark15m' else 'perp'
            first = end-timedelta(days=1) if tag == 'mark15m' else end-dp.WARMUP[interval]-timedelta(days=1)
            raw['candles'][s][tag] = snapshot(market=market, symbol=s, interval=interval, source=SOURCES[market],
                coverage_start=dp.iso(first), coverage_end=dp.iso(end), fetched_at=dp.iso(end), pages=1,
                raw_rows=rows(interval, first, end)).model_dump(mode='json')
    raw['bundle_checksum'] = fingerprint(raw)
    path = tmp_path/'frozen.json'
    path.write_text(encoded(raw))
    return path


def fetchers():
    def candles(query):
        interval = '4h' if query['interval'] == '4h' else '15m'
        start = datetime.fromtimestamp(query['startTime']/1000, timezone.utc)
        end = datetime.fromtimestamp((query['endTime']+1)/1000, timezone.utc)
        return rows(interval, start, end)[:query['limit']]

    def funding(query):
        lo, hi = query['startTime'], query['endTime']
        return [dict(symbol=query['symbol'], fundingTime=t, fundingRate='0.0001', markPrice='100', rateType='Regular')
                for t in range(lo, hi, 8*3_600_000)]
    return dict(
        spot_fetcher=lambda s, i, a, z, now: fetch_spot_snapshot(s, i, a, z, fetch_json=candles, now=now),
        perp_fetcher=lambda s, i, a, z, price_kind, now: fetch_candle_snapshot(
            s, i, a, z, price_kind=price_kind, fetch_json=lambda path, q: candles(q), now=now),
        funding_fetcher=lambda s, a, z, now: fetch_funding_snapshot(s, a, z, fetch_json=funding, now=now))


def test_rule_hash_pins_the_a4_donchian30_10_case():
    cfg = dp.config(FREEZE+timedelta(hours=8))
    assert (cfg.entry_window, cfg.exit_window, cfg.filter_level, cfg.start) == (30, 10, 4, FREEZE)
    other = FilterConfig(start=FREEZE, end=FREEZE+timedelta(hours=8), entry_window=30, exit_window=10, filter_level=3)
    assert dp.rule_hash(other) != dp.RULE_HASH


@pytest.mark.parametrize('now,end', [('13:49', 12), ('16:03', 12), ('16:06', 16)])
def test_latest_end_waits_for_the_published_h4_boundary(now, end):
    at = datetime.fromisoformat(f'2026-10-06T{now}:00+00:00')
    assert dp.latest_end(at) == datetime(2026, 10, 6, end, tzinfo=timezone.utc)


def test_collect_keeps_frozen_warmup_then_replay_scores_insufficient_sample(tmp_path):
    end = FREEZE+timedelta(hours=8)
    inputs = dp.collect(frozen_bundle(tmp_path), tmp_path/'inputs', end, now=NOW, progress=lambda m: None, **fetchers())
    raw = json.loads(inputs.read_text())
    lineage = raw['reuse_lineage']['BTCUSDT']
    assert lineage['spot4h'] == dict(lineage['spot4h'], frozen_rows=600, fetched_rows=2)
    assert lineage['perp15m']['frozen_rows'] == 1936 and lineage['perp15m']['fetched_rows'] == 32
    assert lineage['mark15m']['frozen_rows'] == 0  # Risk marks start at the freeze.
    result = dp.run(inputs, tmp_path/'report', progress=lambda m: None)
    assert result['verdict'] == 'insufficient_sample' and result['deterministic_rerun_verified']
    assert result['rule_hash'] == dp.RULE_HASH and not result['activation_allowed']
    assert json.loads((tmp_path/'report'/'evaluation.json').read_text())['result_id'] == result['result_id']


def test_collect_rejects_a_bundle_that_does_not_end_at_the_freeze(tmp_path):
    with pytest.raises(ValueError, match='end exactly at the freeze'):
        dp.collect(frozen_bundle(tmp_path, FREEZE-timedelta(hours=4)), tmp_path/'inputs', FREEZE+timedelta(hours=8),
                   now=NOW, **fetchers())


def report(days, trades, dd, status='complete'):
    return dict(config=dict(start=FREEZE.isoformat(), end=(FREEZE+timedelta(days=days)).isoformat()),
        status=status, trades=trades, summary=dict(initial_capital=1000.0, max_drawdown_known_pct=dd,
        pnl_after_known_costs=float(sum(D(t['net_pnl']) for t in trades))))


def trade(net, cost='0.5'):
    return dict(net_pnl=net, exchange_fee=cost, slippage_cost=cost, exit_reason='donchian_exit')


@pytest.mark.parametrize('days,trades,dd,verdict,failed', [
    (30, [trade('5'), trade('-2')]*40, 5, 'insufficient_sample', set()),
    (130, [trade('5'), trade('-2')]*40, 5, 'pass', set()),
    (130, [trade('5'), trade('-2')]*40, 13, 'fail', {'max_drawdown'}),
    (130, [trade('2.2', '1'), trade('-2', '1')]*40, 5, 'fail', {'profit_factor', 'net_positive_double_cost'}),
])
def test_evaluate_applies_pre_registered_criteria(days, trades, dd, verdict, failed):
    result = dp.evaluate(report(days, trades, dd))
    assert result['verdict'] == verdict
    assert {k for k, ok in result['checks'].items() if not ok} == failed
