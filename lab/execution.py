"""Market-specific execution adapters for reproducible simulations."""

from decimal import Decimal, ROUND_DOWN

from backtest.engines.global_equity import GlobalEquityEngine


class CryptoSpotEngine(GlobalEquityEngine):
    """Long-only spot execution with frozen Binance symbol rules."""

    def __init__(self, config):
        super().__init__(config, market="crypto")
        self.quantity_step = Decimal(str(config["quantity_step"]))
        self.min_notional = Decimal(str(config["min_notional"]))
        self.taker_fee_rate = Decimal(str(config.get("taker_fee_bps", 10))) / Decimal("10000")
        if self.quantity_step <= 0 or self.min_notional <= 0:
            raise ValueError("Crypto exchange rules must be positive")
        if self.default_leverage != 1:
            raise ValueError("Crypto spot engine forbids leverage")

    def round_size(self, raw_size, price):
        raw = Decimal(str(max(raw_size, 0)))
        quantity = (raw / self.quantity_step).to_integral_value(rounding=ROUND_DOWN) * self.quantity_step
        if quantity * Decimal(str(price)) < self.min_notional:
            return 0.0
        return float(quantity)

    def calc_commission(self, size, price, direction, is_open):
        del direction, is_open
        return float(Decimal(str(abs(size))) * Decimal(str(price)) * self.taker_fee_rate)
