from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from typing import Literal

from intraday.replay_v2.contracts import Candle, FundingHistory, FundingSettlement
from intraday.replay_v2.donchian_filter_engine import prepare, simulate
from intraday.replay_v2.donchian_wide_config import WideSlotConfig, WideSlotStressConfig, wide_cases
from intraday.replay_v2.intraday_timeframes import IntradayCandle

START = datetime(2026, 1, 1, tzinfo=timezone.utc)


class Loose(WideSlotConfig):
    """Synthetic flat candles cannot satisfy ADX/volume filters; the slot logic is what is under test."""
    filter_level: Literal[0] = 0


class LooseRandom(Loose):
    pass


def symbols(count=8):
    return tuple(f'A{i}{"XYZ"[i % 3]}USDT' for i in range(count))


def market_data(syms, *, days=1, boost=None, spot_volume=None):
    """Spot jumps up and Perp jumps down on the bar before START, so both markets break out for every coin.
    `spot_volume[k]` / `boost` raise the trigger-bar volume of coin k and so its rank."""
    data, funding = {}, {}
    end = START+timedelta(days=days)
    for k, symbol in enumerate(syms):
        data[symbol] = {}
        for market, price in (('spot', D(110)), ('perp', D(90))):
            h4 = []
            for i in range(-600, days*6):
                at = START+timedelta(hours=4*i)
                p = price if i >= -1 else D(100)
                volume = D(160)
                if i == -1:
                    volume = D(160)+(D(spot_volume[k]) if (spot_volume and market == 'spot') else D(0))
                    volume += D(boost.get(k, 0)) if (boost and market == 'perp') else D(0)
                h4.append(Candle(opened_at=at, available_at=at+timedelta(hours=4), open=p, high=p+1, low=p-1,
                                 close=p, volume=volume))
            data[symbol][market+'4h'] = tuple(h4)
            small = []
            for i in range(-1936, days*96):
                at = START+timedelta(minutes=15*i)
                p = price if i >= -16 else D(100)
                small.append(IntradayCandle(interval='15m', opened_at=at, available_at=at+timedelta(minutes=15),
                                            open=p, high=p+1, low=p-1, close=p, volume=D(10)))
            data[symbol][market+'15m'] = tuple(small)
        data[symbol]['mark15m'] = tuple(c for c in data[symbol]['perp15m'] if c.opened_at >= START)
        funding[symbol] = FundingHistory(symbol=symbol, source='fixture', coverage_start=START, coverage_end=end,
            settlements=tuple(FundingSettlement(at=START+timedelta(hours=h), rate=0, mark=90)
                              for h in range(0, days*24, 8)))
    return data, funding


def run(config_type=Loose, *, days=1, count=8, boost=None, spot_volume=None, **change):
    syms = symbols(count)
    data, funding = market_data(syms, days=days, boost=boost, spot_volume=spot_volume)
    config = config_type(start=START, end=START+timedelta(days=days), symbols=syms, **change)
    return config, simulate(config, prepare(config, data, funding))


def entries(report):
    return [e for e in report['events'] if e['kind'] == 'entry']


def test_only_the_best_ranked_five_enter_and_the_rest_are_blocked_by_the_slot_limit():
    syms = symbols()
    config, report = run(spot_volume={k: 10*k for k in range(8)}, boost={0: 1000, 1: 1000})
    picked = [(e['market'], e['symbol']) for e in entries(report)]
    assert len(picked) == 5 and len({s for _, s in picked}) == 5
    assert {('perp', syms[0]), ('perp', syms[1])} <= set(picked)       # Highest ratios sit in Perp
    assert {('spot', syms[7]), ('spot', syms[6]), ('spot', syms[5])} == set(picked)-{('perp', syms[0]), ('perp', syms[1])}
    selection = next(e for e in report['events'] if e['kind'] == 'selection')
    assert selection['free_slots'] == 5 and sum(r['granted'] for r in selection['ranked']) == 5
    blocked = [e for e in report['events'] if e['kind'] == 'entry_blocked' and e['reason'] == 'slot_limit']
    assert len(blocked) == len(selection['ranked'])-5


def test_open_positions_never_exceed_the_slot_count_and_each_slot_is_a_fifth_of_the_pool():
    config, report = run(days=3, spot_volume={k: 10*k for k in range(8)})
    points = sorted({t['opened_at'] for t in report['trades']} | {t['closed_at'] for t in report['trades']})
    for at in points:
        live = [t for t in report['trades'] if t['opened_at'] <= at < t['closed_at']]
        assert len(live) <= 5
    spot = [e for e in entries(report) if e['market'] == 'spot']
    multipliers = {e['symbol']: D(e['atr_size_multiplier']) for e in report['events']
                   if e['kind'] == 'entry_features' and e['market'] == 'spot'}
    assert spot and all(abs(D(e['requested_notional'])-D(200)*multipliers[e['symbol']]) < D('1e-20') for e in spot)


def test_one_coin_never_holds_two_positions_and_ranking_is_deterministic():
    _, first = run(spot_volume={k: 10*k for k in range(8)})
    _, second = run(spot_volume={k: 10*k for k in range(8)})
    assert first['events'] == second['events'] and first['trades'] == second['trades']
    held = [e['symbol'] for e in entries(first)]
    assert len(held) == len(set(held))


def test_random_selection_is_seeded_and_differs_from_the_ranked_choice():
    volumes = {k: 10*k for k in range(8)}
    ranked = {(e['market'], e['symbol']) for e in entries(run(spot_volume=volumes)[1])}
    draws = {}
    for seed in ('seed-a', 'seed-b', 'seed-c'):
        _, one = run(spot_volume=volumes, ranker='random', seed=seed)
        _, two = run(spot_volume=volumes, ranker='random', seed=seed)
        assert one['events'] == two['events']
        draws[seed] = {(e['market'], e['symbol']) for e in entries(one)}
    assert any(chosen != ranked for chosen in draws.values())
    assert all(len(chosen) == 5 for chosen in draws.values())


def test_wide_cases_cover_the_registered_family_and_keep_h4_and_h1_preparations_adjacent():
    cases = wide_cases(START, START+timedelta(days=30), symbols())
    names = [n for n, _ in cases]
    assert len(names) == 14 and names[0] == 'WIDE-H4' and names[2] == 'WIDE-H4-random'
    keys = [(c.bar_interval, c.ema_fast, c.ema_slow, c.regime_interval) for _, c in cases]
    seen, previous = set(), None
    for key in keys:
        if key != previous:
            assert key not in seen
            seen.add(key)
        previous = key
    stressed = dict(cases)['WIDE-H1-GEOM-cost2x']
    assert isinstance(stressed, WideSlotStressConfig) and stressed.stop_atr_multiple == 6 and stressed.exit_window == 40
    assert abs(sum(cases[0][1].weights.values())-1) == 0
