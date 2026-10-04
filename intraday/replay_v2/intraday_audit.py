"""Passive common-M15 valuation from frozen cash/fills; never drives execution."""

from datetime import datetime, timedelta
from decimal import Decimal

from intraday.replay_v2.portfolio_book import ONE, ZERO


def audit_drawdown(report, spot, native):
    start, end = (datetime.fromisoformat(report['config'][k]) for k in ('start', 'end'))
    initial = Decimal(report['config']['capital'])
    perp_initial = initial*Decimal(report['config']['perp_cap'])
    ticks, changes, spot_updates = {}, [], {}
    for s, series in native.items():
        for bar in series['mark15m']:
            ticks.setdefault(bar.opened_at, {})[s] = bar.open
            ticks.setdefault(bar.available_at-timedelta(milliseconds=1), {})[s] = bar.close
    ticks[end] = {}
    for s, bars in spot.items():
        for bar in bars:
            if start <= bar.opened_at < end:
                spot_updates.setdefault(bar.opened_at, {})[s] = bar.open
                spot_updates.setdefault(bar.available_at-timedelta(milliseconds=1), {})[s] = bar.close
    for trade in report['trades']:
        if trade['opened_at'] == trade['closed_at']:
            # Immediate cost-triggered flatten: cash includes both fills, but
            # no position survives for post-event common-grid valuation.
            continue
        changes.append((datetime.fromisoformat(trade['opened_at']), 1, trade))
        changes.append((datetime.fromisoformat(trade['closed_at']), 0, trade))
    changes.sort(key=lambda item:(item[0], item[1], item[2]['market'], item[2]['symbol']))
    snapshots = report['equity_curve']
    parent_times = [datetime.fromisoformat(row['at']) for row in snapshots]
    spot_times = sorted(spot_updates)
    if any(a > b for a,b in zip(parent_times, parent_times[1:])):
        raise ValueError('audit requires chronological frozen cash observations')
    cash, perp_cash, flows = initial, perp_initial, ZERO
    flow_adjusted = report['config'].get('allocation_basis') == 'perp-margin'
    parent_index = change_index = spot_index = 0
    positions, spot_marks, perp_marks, curve = {}, {}, {}, []
    peak, perp_peak, dd, perp_dd = initial, perp_initial, ZERO, ZERO
    worst_at = perp_worst_at = None
    for at, updates in sorted(ticks.items()):
        while parent_index < len(snapshots) and parent_times[parent_index] <= at:
            row = snapshots[parent_index]
            cash, perp_cash = Decimal(row['cash']), Decimal(row['perp_realized_capital'])
            if flow_adjusted:
                flows = Decimal(row['perp_capital_flows'])
            parent_index += 1
        while change_index < len(changes) and changes[change_index][0] <= at:
            _, entering, trade = changes[change_index]
            key = (trade['market'], trade['symbol'])
            if entering:
                if key in positions:
                    raise ValueError('overlapping frozen positions in audit')
                positions[key] = trade
            else:
                if key not in positions:
                    raise ValueError('audit exit lacks a frozen entry')
                positions.pop(key)
            change_index += 1
        while spot_index < len(spot_times) and spot_times[spot_index] <= at:
            spot_marks.update(spot_updates[spot_times[spot_index]])
            spot_index += 1
        perp_marks.update(updates)
        spot_value = unrealized = ZERO
        for (market, s), trade in positions.items():
            quantity = Decimal(trade['quantity'])
            if market == 'spot':
                spot_value += quantity*spot_marks[s]
            else:
                sign = 1 if trade['side'] == 'long' else -1
                unrealized += sign*quantity*(perp_marks[s]-Decimal(trade['entry_price']))
        equity, perp_equity = cash+spot_value+unrealized, perp_cash+unrealized
        performance = perp_equity-flows
        peak, perp_peak = max(peak, equity), max(perp_peak, performance)
        current = ONE-equity/peak
        perp_current = ONE-performance/perp_peak if perp_peak else ZERO
        if current > dd:
            dd, worst_at = current, at.isoformat()
        if perp_current > perp_dd:
            perp_dd, perp_worst_at = perp_current, at.isoformat()
        curve.append(dict(at=at.isoformat(), equity_known=str(equity), perp_equity=str(perp_equity),
            cash=str(cash), drawdown=str(current), perp_drawdown=str(perp_current)))
        if flow_adjusted:
            curve[-1].update(perp_performance_equity=str(performance), perp_capital_flows=str(flows))
    if positions or abs(cash-Decimal(str(report['summary']['final_equity_known']))) > Decimal('1e-10'):
        raise ValueError('passive audit does not reconcile to flat final cash')
    audit = dict(samples=len(curve), sampling='native M15 mark open/close, end; post-event as-of cash/fills',
        spot_valuation='latest known native H4 open/close as-of; no interpolated Spot prices',
        passive=True, execution_feedback=False, max_drawdown_pct=float(dd*100), worst_at=worst_at,
        perp_max_drawdown_pct=float(perp_dd*100), perp_worst_at=perp_worst_at,
        final_equity=float(equity), final_perp_equity=float(perp_equity),
        limitation='common-grid audit is not exact intrabar DD; Spot remains H4 as-of')
    if flow_adjusted:
        audit.update(perp_performance_flow_adjusted=True, final_perp_performance_equity=float(performance))
    return audit, curve
