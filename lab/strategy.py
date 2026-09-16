"""Causal close-of-day targets. Execution happens on the following bar."""

import numpy as np
import pandas as pd

from lab.contracts import (
    DonchianStrategySpec,
    EntryVolatilityPositionSizingSpec,
    FixedPositionSizingSpec,
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


def position_sizing_from_dict(value):
    if value is None:
        return FixedPositionSizingSpec()
    if isinstance(value, (FixedPositionSizingSpec, EntryVolatilityPositionSizingSpec)):
        return value
    family = value.get("family") if isinstance(value, dict) else None
    models = {
        "fixed": FixedPositionSizingSpec,
        "entry_volatility": EntryVolatilityPositionSizingSpec,
    }
    if family not in models:
        raise ValueError(f"Unsupported position sizing family: {family!r}")
    return models[family].model_validate(value)


def _stateful_target(entry, exit_, weight):
    target = pd.Series(0.0, index=entry.index, name="target")
    weights = (
        pd.Series(float(weight), index=entry.index)
        if np.isscalar(weight)
        else pd.Series(weight, index=entry.index, dtype=float)
    )
    held_weight = 0.0
    for index in entry.index:
        if held_weight > 0 and bool(exit_.loc[index]):
            held_weight = 0.0
        elif held_weight == 0 and bool(entry.loc[index]):
            candidate = float(weights.loc[index])
            if np.isfinite(candidate) and candidate > 0:
                held_weight = candidate
        target.loc[index] = held_weight
    return target


class RuleSignalEngine:
    def __init__(
        self, strategy, target_weight=1.0, buy_and_hold=False, position_sizing=None,
    ):
        self.strategy = strategy_from_dict(strategy)
        self.target_weight = float(target_weight)
        if not 0 < self.target_weight <= 1:
            raise ValueError("target_weight must be in (0, 1]")
        self.buy_and_hold = bool(buy_and_hold)
        self.position_sizing = position_sizing_from_dict(position_sizing)

    @property
    def warmup_sessions(self):
        sizing_warmup = (
            self.position_sizing.lookback + 1
            if isinstance(self.position_sizing, EntryVolatilityPositionSizingSpec)
            else 0
        )
        return max(self.strategy.warmup_sessions, sizing_warmup)

    def _sizing_evidence(self, close):
        if isinstance(self.position_sizing, FixedPositionSizingSpec):
            volatility = pd.Series(np.nan, index=close.index, dtype=float)
            entry_weight = pd.Series(self.target_weight, index=close.index, dtype=float)
        else:
            log_returns = np.log(close / close.shift(1))
            volatility = (
                log_returns.rolling(self.position_sizing.lookback).std(ddof=0)
                * np.sqrt(self.position_sizing.annualization_days)
            )
            valid = np.isfinite(volatility) & (volatility > 1e-12)
            entry_weight = (
                self.position_sizing.annual_target / volatility
            ).clip(upper=self.target_weight).where(valid, 0.0)
        return volatility, entry_weight

    def _evidence_frame(self, values, volatility, entry_weight):
        values.update({
            "realized_volatility": volatility,
            "entry_weight": entry_weight,
            "position_sizing_family": self.position_sizing.family,
        })
        return pd.DataFrame(values, index=volatility.index)

    def evidence(self, frame):
        close = frame["close"].astype(float)
        volatility, entry_weight = self._sizing_evidence(close)
        if self.buy_and_hold:
            target = close.notna().astype(float) * self.target_weight
            return self._evidence_frame(
                {"close": close, "entry": close.notna(), "exit": False, "target": target},
                volatility,
                entry_weight,
            )
        if isinstance(self.strategy, SmaStrategySpec):
            fast = close.rolling(self.strategy.fast_window).mean()
            slow = close.rolling(self.strategy.slow_window).mean()
            entry = fast > slow
            exit_ = fast <= slow
            target = _stateful_target(entry, exit_, entry_weight)
            return self._evidence_frame(
                {"close": close, "sma_fast": fast, "sma_slow": slow,
                 "entry": entry, "exit": exit_, "target": target},
                volatility,
                entry_weight,
            )
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
            target = _stateful_target(entry.fillna(False), exit_.fillna(False), entry_weight)
            return self._evidence_frame({
                "close": close, "rsi": rsi, "bollinger_mid": mid,
                "bollinger_lower": lower, "bollinger_upper": upper,
                "entry": entry, "exit": exit_, "target": target,
            }, volatility, entry_weight)
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
        target = _stateful_target(entry.fillna(False), exit_.fillna(False), entry_weight)
        return self._evidence_frame({
            "close": close, "donchian_entry": entry_channel,
            "donchian_exit": exit_channel, "atr": atr,
            "entry": entry, "exit": exit_, "target": target,
        }, volatility, entry_weight)

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
