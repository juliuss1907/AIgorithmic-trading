"""Opt-in close-sampled net-PnL trailing per trade; no exchange stop orders."""

from dataclasses import dataclass
from decimal import Decimal

from intraday.replay_v2.mixed_book import PERP_FEE, PERP_SLIP
from intraday.replay_v2.perp_daily import PerpDailyBook
from intraday.replay_v2.portfolio_book import ZERO


ARM, GIVEBACK = Decimal('.03'), Decimal('.03')


@dataclass
class TradeTrailingState:
    peak: Decimal = ZERO
    floor: Decimal | None = None
    triggered_at: str | None = None

    def observe(self, net_return):
        self.peak = max(self.peak, net_return)
        if self.peak >= ARM:
            candidate = self.peak-GIVEBACK
            self.floor = candidate if self.floor is None else max(self.floor, candidate)

    def breached(self, net_return):
        return self.floor is not None and net_return <= self.floor


class TradeTrailingBook(PerpDailyBook):
    def __init__(self, config):
        super().__init__(config)
        self.trade_trailing = {}

    def enter_perp(self, symbol, at, marks, price, side, stop_distance, *, approved_target=None):
        entered = super().enter_perp(symbol, at, marks, price, side, stop_distance,
                                     approved_target=approved_target)
        if entered:
            self.trade_trailing[symbol] = TradeTrailingState()
        return entered

    def projected_trade_return(self, symbol, price):
        p = self.perps[symbol]
        net = (p.quantity*(price-p.entry_price)-p.entry_fee-p.entry_slip-p.funding_paid
               -abs(p.quantity)*price*(PERP_FEE+PERP_SLIP))
        return net/(abs(p.quantity)*p.entry_price)

    def trailing_fields(self, symbol):
        state = self.trade_trailing[symbol]
        return dict(trailing_peak_return=str(state.peak),
                    trailing_floor_return=str(state.floor) if state.floor is not None else None)

    def trigger_trailing(self, symbol, at, net_return, stage):
        state = self.trade_trailing[symbol]
        if state.triggered_at is None:
            state.triggered_at = at.isoformat()
            self.event(at, 'perp_trade_trailing_trigger', 'perp_trade_trailing', symbol=symbol,
                       market='perp', stage=stage, projected_net_return=str(net_return),
                       **self.trailing_fields(symbol))

    def observe_trade_trailing(self, at, bars):
        for symbol, bar in sorted(bars.items()):
            if not self.perps[symbol].quantity:
                continue
            state = self.trade_trailing[symbol]
            before = state.floor
            net_return = self.projected_trade_return(symbol, bar.close)
            state.observe(net_return)
            if state.floor != before:
                self.event(at, 'perp_trade_trailing_update', 'net_peak_minus_3pp', symbol=symbol,
                           market='perp', projected_net_return=str(net_return), **self.trailing_fields(symbol))
            if state.breached(net_return):
                self.trigger_trailing(symbol, at, net_return, 'close')

    def perp_exit_reason(self, symbol, price, at):
        if not self.perps[symbol].quantity:
            return None
        state = self.trade_trailing[symbol]
        net_return = self.projected_trade_return(symbol, price)
        if state.triggered_at is not None or state.breached(net_return):
            self.trigger_trailing(symbol, at, net_return, 'open')
            return 'perp_trade_trailing'
        return None

    def close_perp(self, symbol, at, price, reason):
        p = self.perps[symbol]
        if not p.quantity:
            return
        fields = self.trailing_fields(symbol)
        state = self.trade_trailing[symbol]
        notional = abs(p.quantity)*p.entry_price
        holding_hours = (at-p.entered_at).total_seconds()/3600
        super().close_perp(symbol, at, price, reason)
        self.trades[-1].update(entry_notional=notional, holding_hours=holding_hours,
            trailing_peak_return=Decimal(fields['trailing_peak_return']),
            trailing_floor_return=state.floor, trailing_triggered_at=state.triggered_at)
        self.trade_trailing.pop(symbol)
