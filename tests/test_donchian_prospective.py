import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from intraday.replay_v2 import donchian_prospective as dp
from intraday.replay_v2.donchian_adx_setups import ADXSetupConfig, specification
from intraday.replay_v2.donchian_filter_data import fetch_spot_snapshot
from intraday.replay_v2.funding import fetch_funding_snapshot
from intraday.replay_v2.historical_data import fetch_candle_snapshot

FREEZE = dp.FREEZE
WIDTH = {'4h': 14_400_000, '15m': 900_000}


def rows(interval, start, end, price='100'):
    w, lo, hi = WIDTH[interval], int(start.timestamp()*1000), int(end.timestamp()*1000)
    return [[t, price, str(int(price)+1), str(int(price)-1), price, '10', t+w-1] for t in range(lo, hi, w)]


def fetchers():
    def candles(query):
        interval = '4h' if query['interval'] == '4h' else '15m'
        start = datetime.fromtimestamp(query['startTime']/1000, timezone.utc)
        end = datetime.fromtimestamp((query['endTime']+1)/1000, timezone.utc)
        return rows(interval, start, end)[:query['limit']]

    def funding(query):
        return [dict(symbol=query['symbol'], fundingTime=t, fundingRate='0.0001', markPrice='100', rateType='Regular')
                for t in range(query['startTime'], query['endTime'], 8*3_600_000)]
    return dict(
        spot_fetcher=lambda s, i, a, z, now: fetch_spot_snapshot(s, i, a, z, fetch_json=candles, now=now),
        perp_fetcher=lambda s, i, a, z, price_kind, now: fetch_candle_snapshot(
            s, i, a, z, price_kind=price_kind, fetch_json=lambda path, q: candles(q), now=now),
        funding_fetcher=lambda s, a, z, now: fetch_funding_snapshot(s, a, z, fetch_json=funding, now=now))


def test_rule_hash_pins_setup_2_near_sol_zec_adx20():
    cfg = dp.config(FREEZE+timedelta(hours=8))
    assert dp.symbols() == ['NEARUSDT', 'SOLUSDT', 'ZECUSDT']
    assert cfg.weights == {'NEARUSDT': D('.3'), 'SOLUSDT': D('.4'), 'ZECUSDT': D('.3')} == cfg.perp_weights
    assert cfg.adx_by_market == {m: dict.fromkeys(dp.symbols(), 20) for m in ('spot', 'perp')}
    other = ADXSetupConfig(start=FREEZE, end=FREEZE+timedelta(hours=8), setup=1, **specification(1))
    assert dp.rule_hash(other) != dp.RULE_HASH
    assert dp.config(FREEZE+timedelta(hours=8), stress=True).cost_multiplier == 2


@pytest.mark.parametrize('now,end', [('2026-10-08T04:04', 'too early'), ('2026-10-08T04:06', 4), ('2026-10-09T13:49', 36)])
def test_latest_end_waits_for_a_published_h4_boundary_after_freeze(now, end):
    at = datetime.fromisoformat(now+':00+00:00')
    if end == 'too early':
        with pytest.raises(ValueError, match='no complete H4'):
            dp.latest_end(at)
    else:
        assert dp.latest_end(at) == FREEZE+timedelta(hours=end)


def test_collect_fetches_warmup_and_replay_scores_insufficient_sample(tmp_path):
    end = FREEZE+timedelta(hours=8)
    inputs = dp.collect(tmp_path/'inputs', end, now=end+timedelta(hours=1), progress=lambda m: None, **fetchers())
    raw = json.loads(inputs.read_text())
    assert sorted(raw['candles']) == dp.symbols()
    near = raw['candles']['NEARUSDT']
    assert (len(near['spot4h']['raw_rows']), len(near['perp15m']['raw_rows']), len(near['mark15m']['raw_rows'])) == (602, 1968, 32)
    result = dp.run(inputs, tmp_path/'report', progress=lambda m: None)
    assert result['verdict'] == 'insufficient_sample' and result['deterministic_rerun_verified']
    assert result['rule_hash'] == dp.RULE_HASH and not result['activation_allowed']
    assert result['stress_run_id'] != result['run_id']
    assert json.loads((tmp_path/'report'/'evaluation.json').read_text())['result_id'] == result['result_id']


def test_a_window_not_starting_at_the_freeze_is_only_a_pipeline_check(tmp_path):
    start = FREEZE-timedelta(days=30)
    end = start+timedelta(hours=8)
    inputs = dp.collect(tmp_path/'inputs', end, start=start, now=end+timedelta(hours=1), progress=lambda m: None, **fetchers())
    assert dp.run(inputs, tmp_path/'report', progress=lambda m: None)['verdict'] == 'not_prospective'


def report(days, trades, dd, status='complete'):
    return dict(config=dict(start=FREEZE.isoformat(), end=(FREEZE+timedelta(days=days)).isoformat()),
        status=status, trades=trades, summary=dict(initial_capital=1000.0, max_drawdown_known_pct=dd,
        pnl_after_known_costs=float(sum(D(t['net_pnl']) for t in trades))))


def trade(net):
    return dict(net_pnl=net, exit_reason='donchian_exit')


@pytest.mark.parametrize('days,trades,dd,stress_net,verdict,failed', [
    (30, [trade('5'), trade('-2')]*40, 5, 10, 'insufficient_sample', set()),
    (130, [trade('5'), trade('-2')]*40, 5, 10, 'pass', set()),
    (130, [trade('5'), trade('-2')]*40, 13, 10, 'fail', {'max_drawdown'}),
    (130, [trade('2.2'), trade('-2')]*40, 5, -3, 'fail', {'profit_factor', 'net_positive_double_cost'}),
])
def test_evaluate_applies_pre_registered_criteria(days, trades, dd, stress_net, verdict, failed):
    stress = dict(status='complete', summary=dict(pnl_after_known_costs=stress_net))
    result = dp.evaluate(report(days, trades, dd), stress)
    assert result['verdict'] == verdict
    assert {k for k, ok in result['checks'].items() if not ok} == failed


class Bot:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def send(self, text):
        if self.fail:
            raise RuntimeError('down')
        self.sent.append(text)


def test_scheduled_runs_once_per_published_h4_end(tmp_path):
    now = FREEZE+timedelta(hours=8, minutes=6)
    quiet, bot = (lambda m: None), Bot()
    assert dp.scheduled(tmp_path, now=FREEZE+timedelta(hours=4), progress=quiet, notifier=bot, **fetchers()) is None
    first = dp.scheduled(tmp_path, now=now, progress=quiet, notifier=bot, **fetchers())
    assert first['verdict'] == 'insufficient_sample' and first['window_end'] == (FREEZE+timedelta(hours=8)).isoformat()
    assert sorted(p.name for p in tmp_path.iterdir()) == ['setup2-prospective-20261008T0800Z',
                                                         'setup2-prospective-20261008T0800Z-inputs']
    assert dp.scheduled(tmp_path, now=now, progress=quiet, notifier=bot, **fetchers()) == first
    assert len(bot.sent) == 1 and bot.sent[0].startswith('Setup-2 paper: insufficient_sample')


def test_scheduled_refuses_an_incomplete_report_directory_and_alerts(tmp_path):
    (tmp_path/'setup2-prospective-20261008T0400Z').mkdir()
    bot = Bot()
    with pytest.raises(ValueError, match='incomplete report'):
        dp.scheduled(tmp_path, now=FREEZE+timedelta(hours=4, minutes=6), progress=lambda m: None,
                     notifier=bot, **fetchers())
    assert len(bot.sent) == 1 and 'incomplete report' in bot.sent[0]


def test_a_failed_alert_never_fails_the_run(tmp_path):
    seen = []
    result = dp.scheduled(tmp_path, now=FREEZE+timedelta(hours=4, minutes=6), progress=seen.append,
                          notifier=Bot(fail=True), **fetchers())
    assert result['verdict'] == 'insufficient_sample'
    assert 'telegram delivery failed: RuntimeError' in seen


def test_notifier_needs_both_telegram_variables():
    assert dp.notifier_from_env({'TELEGRAM_BOT_TOKEN': 'x'}) is None
    assert dp.notifier_from_env({'TELEGRAM_BOT_TOKEN': '1:a', 'TELEGRAM_CHAT_ID': '2'}) is not None
