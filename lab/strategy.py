"""Causal close-of-day targets. Execution happens on the following bar."""

import numpy as np
import pandas as pd

from lab.contracts import (
    DonchianStrategySpec,
    RsiBollingerStrategySpec,
    SmaStrategySpec,
)


def strategy_from_dict(value):
    if isinstance(value, (SmaStrategySpec, RsiBollingerStrategySpec, DonchianStrategySpec)):
        return value
    family = value.get("family") if isinstance(value, dict) else None
    models = {
        "sma_crossover": SmaStrategySpec,
        "rsi_bollinger": RsiBollingerStrategySpec,
        "donchian_breakout": DonchianStrategySpec,
    }
    if family not in models:
        raise ValueError(f"Unsupported strategy family: {family!r}")
    return models[family].model_validate(value)


def _stateful_target(entry, exit_, weight):
    target = pd.Series(0.0, index=entry.index, name="target")
    held = False
    for index in entry.index:
        if held and bool(exit_.loc[index]):
            held = False
        elif not held and bool(entry.loc[index]):
            held = True
        target.loc[index] = weight if held else 0.0
    return target


class RuleSignalEngine:
    def __init__(self, strategy, target_weight=1.0, buy_and_hold=False):
        self.strategy = strategy_from_dict(strategy)
        self.target_weight = float(target_weight)
        if not 0 < self.target_weight <= 1:
            raise ValueError("target_weight must be in (0, 1]")
        self.buy_and_hold = bool(buy_and_hold)

    def evidence(self, frame):
        close = frame["close"].astype(float)
        if self.buy_and_hold:
            target = close.notna().astype(float) * self.target_weight
            return pd.DataFrame({"close": close, "entry": close.notna(), "exit": False,
                                 "target": target}, index=frame.index)
        if isinstance(self.strategy, SmaStrategySpec):
            fast = close.rolling(self.strategy.fast_window).mean()
            slow = close.rolling(self.strategy.slow_window).mean()
            target = (fast > slow).astype(float) * self.target_weight
            return pd.DataFrame({"close": close, "sma_fast": fast, "sma_slow": slow,
                                 "entry": fast > slow, "exit": fast <= slow,
                                 "target": target}, index=frame.index)
        if isinstance(self.strategy, RsiBollingerStrategySpec):
            delta = close.diff()
            gains = delta.clip(lower=0)
            losses = -delta.clip(upper=0)
            avg_gain = gains.ewm(
                alpha=1 / self.strategy.rsi_window,
                min_periods=self.strategy.rsi_window,
                adjust=False,
            ).mean()
            avg_loss = losses.ewm(
                alpha=1 / self.strategy.rsi_window,
                min_periods=self.strategy.rsi_window,
                adjust=False,
            ).mean()
            relative_strength = avg_gain / avg_loss.replace(0, np.nan)
            rsi = 100 - 100 / (1 + relative_strength)
            rsi = rsi.mask((avg_loss == 0) & (avg_gain > 0), 100)
            rsi = rsi.mask((avg_loss == 0) & (avg_gain == 0), 50)
            mid = close.rolling(self.strategy.bollinger_window).mean()
            deviation = close.rolling(self.strategy.bollinger_window).std(ddof=0)
            lower = mid - self.strategy.bollinger_stddev * deviation
            upper = mid + self.strategy.bollinger_stddev * deviation
            entry = (close < lower) & (rsi < self.strategy.entry_rsi)
            exit_ = (close >= mid) | (rsi > self.strategy.exit_rsi)
            target = _stateful_target(entry.fillna(False), exit_.fillna(False), self.target_weight)
            return pd.DataFrame({
                "close": close, "rsi": rsi, "bollinger_mid": mid,
                "bollinger_lower": lower, "bollinger_upper": upper,
                "entry": entry, "exit": exit_, "target": target,
            }, index=frame.index)
        if not {"high", "low"}.issubset(frame):
            raise ValueError("Donchian strategy requires high and low prices")
        high = frame["high"].astype(float)
        low = frame["low"].astype(float)
        entry_channel = high.shift(1).rolling(self.strategy.entry_window).max()
        exit_channel = low.shift(1).rolling(self.strategy.exit_window).min()
        previous_close = close.shift(1)
        true_range = pd.concat([
            high - low, (high - previous_close).abs(), (low - previous_close).abs()
        ], axis=1).max(axis=1)
        atr = true_range.ewm(
            alpha=1 / self.strategy.atr_window,
            min_periods=self.strategy.atr_window,
            adjust=False,
        ).mean()
        entry = close > entry_channel
        exit_ = close < exit_channel
        target = _stateful_target(entry.fillna(False), exit_.fillna(False), self.target_weight)
        return pd.DataFrame({
            "close": close, "donchian_entry": entry_channel,
            "donchian_exit": exit_channel, "atr": atr,
            "entry": entry, "exit": exit_, "target": target,
        }, index=frame.index)

    def generate(self, data_map):
        return {symbol: self.evidence(frame)["target"] for symbol, frame in data_map.items()}


class SignalEngine(RuleSignalEngine):
    """Backward-compatible SMA interface used by the original SPY runs."""

    def __init__(self, fast=20, slow=50, buy_and_hold=False):
        try:
            strategy = SmaStrategySpec(fast_window=fast, slow_window=slow)
        except ValueError as exc:
            raise ValueError("Expected integer windows: 0 < fast < slow") from exc
        self.fast = fast
        self.slow = slow
        super().__init__(strategy, 1.0, buy_and_hold)
