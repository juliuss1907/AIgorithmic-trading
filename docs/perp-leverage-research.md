# Perp leverage experiment — pre-registration

Registered 2026-10-13 before collecting the recent window or replaying the leveraged historical cases.
Specification: `docs/plans/recent-48h-and-perp5x-plan.md` in the parent checkout at base `7e36110`.
Work is isolated on `research/recent-48h-perp5x`; the rotation-capacity study continues in the parent.

Compare ADR-005 H4 with PERP1X and PERP5X: ETH/NEAR/SOL thirds, fresh 1,000 USDT, H4 Setup-2
filters, Perp long and short, 100% realized capital, ATR sizing, ATR14 x3 trail with a 10% price cap,
combined UTC daily loss 3%, drawdown observed. Run ten normal-cost cases over the latest 48 complete
hours, then the three historical cases over [2022-01-01, 2026-10-02) and [2026-01-01, 2026-10-02).
The historical verdict uses the existing five rotation criteria against H4, without retuning.

Prediction: PERP5X profit and drawdown will both be much larger than PERP1X; daily loss locks will
occur frequently, and liquidations are possible. This is a hypothesis, not an activation request.

The fixed maintenance rate is 0.4%, with no exchange notional tiers. Liquidation forfeits the entire
entry margin, with costs and funding reconciled once. M15 OHLC cannot establish the order of a mark
liquidation touch and a trade-price stop touch inside the same bar: use liquidation first in that
ambiguous bar, disclose this conservative assumption, and continue the replay. Daily3% remains active.

The recent account ignores positions opened before its start. `window_end` closes are valuation exits,
reported separately from strategy exits. At full size x5, one position has notional about 1.67 times
capital; a 10% price stop costs about 16.7% of capital before costs. Daily3% can bind after adverse
price moves of about 1.8% for one position or 0.6% for three. The hard-stop/liquidation gap is 9.6
percentage points at x5. ATR sizing can reduce these exposures. No orders, deployment or runtime edits.
