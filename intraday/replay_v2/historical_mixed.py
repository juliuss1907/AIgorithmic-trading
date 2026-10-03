"""Historical quantitative Perp research; never fabricates Jev decisions."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from intraday.contracts import SpotRuleParameters
from intraday.replay_v2.mixed_book import MixedConfig
from intraday.replay_v2.portfolio_research import daily_points
from intraday.spot_signal import evaluate_donchian


class HistoricalConfig(MixedConfig):
    perp_stop: Literal['fixed-1pct', 'atr14-2x'] = 'fixed-1pct'


@dataclass(frozen=True)
class PerpObservation:
    entry_side: int
    exit_long: bool
    exit_short: bool
    atr: Decimal


def perp_observation(rows):
    obs = evaluate_donchian(rows, SpotRuleParameters(entry_window=30, exit_window=8, atr_period=14))
    close = Decimal(str(rows[-1][4]))
    low_entry = min(Decimal(str(row[3])) for row in rows[-31:-1])
    high_exit = max(Decimal(str(row[2])) for row in rows[-9:-1])
    return PerpObservation(1 if obs.entry else -1 if close < low_entry else 0,
        obs.exit, close > high_exit, Decimal(str(obs.atr)))


def daily_directions(rows):
    validated = daily_points(rows)
    closes, ema, prior, sides = [], None, None, []
    for row in rows:
        close = Decimal(str(row[4]))
        closes.append(close)
        if len(closes) == 50:
            ema = sum(closes)/50
        elif ema is not None:
            prior = ema
            ema += Decimal(2)/51*(close-ema)
        sides.append(1 if prior is not None and close > ema > prior else
                     -1 if prior is not None and close < ema < prior else 0)
    return [point[0] for point in validated], sides


def stop_fraction(config, entry_price, atr):
    return Decimal('.01') if config.perp_stop == 'fixed-1pct' else 2*atr/entry_price
