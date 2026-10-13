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

## Reproduce

Fresh public collection and all ten recent cases (refuses an existing directory):

```sh
uv run python -m intraday.replay_v2.donchian_recent --hours 48 --report-root <new-root>
# Pin the UTC H4 boundary with --end 2026-10-13T00:00:00+00:00.
```

Historical comparison on the existing approved five-coin manifest (six cases: H4/1x/5x, normal/cost2x):

```sh
uv run python -m intraday.replay_v2.donchian_adx_study --universe perp-leverage \
  --inputs <approved-data/inputs.json> --report-root <new-root>
# Add --start 2026-01-01T00:00:00+00:00 for the fresh 2026-only account.
# --resume verifies source/input/config bindings and every saved journal before continuing.
```

Cost stress is needed for the historical five-criterion verdict; the 48h study has no cost-stress cases.
`spot_and_perp_net_positive` is kept unchanged and necessarily fails for a 100% Perp account with no
Spot PnL. The verdict reports this structural failure rather than silently replacing the criterion.

Read-only accounting and three-control regression:

```sh
uv run python scripts/verify_perp_leverage_research.py \
  --rotation-reference <old-rotation/runs/comparison.json> \
  --comparison <perp-full/runs/comparison.json> \
  --comparison <perp-2026/runs/comparison.json> --output-root <new-verification-root>
```

The verifier checks manifest hashes, recalculates every trade using Decimal, checks fees/slippage
against entry and exit notional, reconciles funding, margin and liquidation forfeitures, and compares
the three old cases against their published result IDs, summaries and all three journal hashes.

The older Rotation reference predates two defaults already present at base `7e36110`:
`limit_scope='total'` (introduced at `2feddc4`) and `slot_divisor=None` (at `7e36110`). These change
serialized result IDs, and the former adds a selection-event metadata field, before this task's code.
The verifier retains actual IDs and raw hashes, reconstructs only those exact prior defaults for the
old-schema comparison, and rejects changed or unknown rule parameters. It never edits an input,
published journal, strategy configuration or frozen rule. A direct-script regression test also ensures
the shared editable virtualenv imports this worktree, rather than the parent checkout.

## Recent 48h result

Window UTC `[2026-10-11T00:00:00Z, 2026-10-13T00:00:00Z)`, or 11 October 07:00 to 13 October 07:00
in Vietnam. Twelve closed H4 bars; each account starts fresh at 1,000 USDT. Indicators use preceding
warmup; an entry at the start can use the immediately preceding closed H4 bar. No pre-window positions
are carried in. Every run ends flat; any `window_end` close is a valuation with assumed closing costs.

All ten cases had **zero orders, zero PnL and zero observed drawdown**. Nine had one indicator-blocked
breakout each; Setup-4 had none. ADR005-H4, both rotation cases, Setup-1/2/3 and Setup-5 blocked the
NEAR Spot breakout at 11 October 16:00 UTC; PERP1X/PERP5X evaluated the corresponding Perp long
breakout. The failed filter was ADX. With no positions, fees/funding/daily locks/stops/liquidations and
maximum locked margin were all zero. This is a sparse descriptive window, not evidence for or against x5.

At that breakout, NEAR Spot ADX was 19.3693 (prior 17.4286), and Perp ADX was 18.6983 (prior 16.6299).
Both rose, but neither exceeded even the ADX20 threshold. Other filters passed; Setup-1's NEAR Spot
threshold is 25, which also failed. These values are reproduced from the checksum-bound three-coin input.

Evidence roots under `~/.local/state/aigorithmic-trading/reports/`:

- `donchian-recent48h-perp5x-20261013-01/`: original collection, three separate input bundles and first replay.
- `donchian-recent48h-perp5x-20261013-02/`: same immutable inputs with the final detailed reporting code.

Each `comparison.json` binds file SHA256, bundle checksum, feature checksum, engine commit and per-case
result ID. Every case was replayed twice; summary, result ID and all equity/trade/event journals matched.
The Markdown report includes orders, blocked signal times/coins/filters, reconstructed position skips,
end-of-window positions and Perp risk/accounting counts. ZEC is the HYPE stand-in specified in the plan.

## Historical results

All six cases in each window were replayed twice with every journal checked. Shared approved inputs:
`~/.local/state/aigorithmic-trading/reports/donchian-adx-five-20261005/approved-data/inputs.json`.
Each historical comparison retains the full immutable snapshot lineage and source binding. No data
join resets the full-window account; the 2026-only window is a separate fresh 1,000-USDT account.

Normal costs; the last column is a separate replay at doubled fees and slippage, with funding unchanged:

| Window UTC | Case | Net return | DD | Trades | Daily locks | 10% stop exits | Liquidations | Return at cost2x |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 2022-01-01 → 2026-10-02 | H4 | +81.75% | 11.58% | 423 | 0 | — | — | +57.39% |
| 2022-01-01 → 2026-10-02 | PERP1X | +129.99% | 21.08% | 438 | 27 | 14 | 0 | +85.89% |
| 2022-01-01 → 2026-10-02 | PERP5X | **−68.12%** | **94.74%** | 582 | 327 | 0 | 0 | **−93.01%** |
| 2026-01-01 → 2026-10-02 | H4 | +14.23% | 10.46% | 65 | 0 | — | — | +11.38% |
| 2026-01-01 → 2026-10-02 | PERP1X | +16.12% | 18.75% | 70 | 6 | 1 | 0 | +12.02% |
| 2026-01-01 → 2026-10-02 | PERP5X | +28.85% | **40.34%** | 91 | 47 | 0 | 0 | +6.96% |

All windows are end-exclusive. Normal-cost accounting in USDT:

| Window | Case | Exchange fees | Assumed slippage | Funding paid | Maximum locked margin |
|---|---|---:|---:|---:|---:|
| Full | PERP1X | 176.72 | 176.72 | 83.67 | 2,013.59 |
| Full | PERP5X | 340.87 | 340.87 | 44.83 | 997.14 |
| 2026 | PERP1X | 20.62 | 20.62 | 5.89 | 1,016.68 |
| 2026 | PERP5X | 126.90 | 126.90 | 13.53 | 1,047.05 |

Margin may exceed initial capital after realized gains. It stays bounded by available realized capital
and cash after notional entry costs. The x5 initial stop/liquidation gap ranged from 9.6 to 17.6965
percentage points in the full window (9.6 to 16.6099 in 2026); 9.6pp is the 10% hard-stop case, while
a closer ATR stop gives a larger gap. The maintenance ratio remains fixed at 0.4%, not exchange tiers.

The x5 account ended with 318.79 USDT at normal costs and 69.89 USDT at doubled costs on the full
window. It had 520 daily-loss exits out of 582 trades (89.35%); in 2026, 84 of 91 exits (92.31%) were
daily loss. Median holding time fell from 56.25h for 1x to 10h for 5x on the full window (13.25h for
5x in 2026). The 3% daily rule acts much earlier than the 10% price stop in this model: x5 had no
hard-stop or liquidation exits in either window. There were no liquidation penalties in these real
data runs; synthetic long/short tests exercise and reconcile that path.

Daily3% is a per-UTC-day rule, not a cumulative capital floor. Repeated losing days can produce a very
large cumulative drawdown; this experiment observes DD rather than halting on a peak drawdown limit.

The profit prediction was rejected on the full window: 5x lost money while 1x gained. The 2026 gain
was only about 1.79 times 1x, with about 2.15 times its DD; it was not five times the same account's
profit. Leverage changes entry budgets, fees, daily locks, exit times and future realized sizing, so
the result is a different capital path. Frequent daily locks matched the prediction; liquidation did
not occur in these windows.

The five unchanged criteria produce `advancing=[]` in both windows. Full-window 1x fails DD<15%
and the structural Spot-positive criterion; full-window 5x fails all five, including normal return,
doubled-cost positivity and both halves positive. In 2026 both fail DD and Spot-positive; 1x also
fails both halves positive. These are seen-data findings, with no change to ADR-005 or approval to trade.

Historical evidence roots under the same private reports directory:

- `donchian-perp-leverage-full-20261013-01/runs/`
- `donchian-perp-leverage-2026ytd-20261013-01/runs/`

Each root contains `binding.json`, six case checkpoints, immutable report manifests and full journals,
`comparison.json`, `comparison.md`, and the five-criterion `verdict.json`. Engine commit for both
historical runs and the final recent report: `47c7cb59970cc6a28f15b2abd00d707f645e6158`.

## Verification and scope

Final audit: `donchian-perp-leverage-verification-20261013-01/regression-02/verification.json`, bound to
this research worktree and verifier commit `9b1d4d414e79a215d24727035e204663aab56fc1`. It independently
verified all 22 published cases, with three old controls reproduced:

| Control | Return | DD | Trades | Raw equity/trade journals | Event journal |
|---|---:|---:|---:|---|---|
| H4 | +81.75% | 11.58% | 423 | Identical | Identical |
| ROT-SLOT | +73.24% | 9.93% | 438 | Identical | Identical under the prior-default schema comparison |
| ZEC-ROT-SLOT | +88.72% | 9.40% | 548 | Identical | Identical under the prior-default schema comparison |

Actual and old-schema result IDs and journal hashes are both retained. For Rotation the only
event normalization is the already-existing `limit_scope='total'` selection metadata, introduced
before this task. Original files remain unchanged. Earlier audit attempts remain at `recent/`,
`full-accounting/`, and `regression/` with explicit superseded/failure receipts; `regression-02/` is final.

Tests:

- Research gate: **292 passed**, 1,233 deselected; covers Donchian/hourly/slowregime/wide/filter and
  the first audit tests. The final expanded audit/tool gate: **9 passed**, including incorrect PnL,
  fees/identity, shared-virtualenv imports, and rejection of actual legacy rule changes.
- Full suite: **1,522 passed, 4 failed** (23 dependency warnings). The four failures are outside the
  changed research code: two Compact A/B physical SQLite-file hash assertions, missing Hermes
  `.env.template`, and collection readiness returning `v1` where the old test expects `gate-v2.1`.
- A clean detached checkout of base `7e36110` produced **1,482 passed, 3 failed**, reproducing the
  Compact scoring, Hermes template and readiness failures. Both Compact tests pass in isolation
  in both checkouts; a baseline-only fixture probe proved garbage collection alone changes the
  SQLite file hash. Existing WAL connection/checkpoint timing makes those byte-hash assertions
  timing-sensitive. Store, Compact A/B, readiness, gates and runtime were left untouched.

Full-suite and baseline logs, plus the fixture-only GC receipt, are retained under
`donchian-perp-leverage-verification-20261013-01/`. The full-suite count predates five additional
CLI/schema tests; those passed in the final nine-test focused run. No tests were disabled or loosened.

Protected files match base: `donchian_prospective.py` (`FREEZE` and ADR-005 `RULE_HASH`),
`intraday/contracts.py`, `intraday/active_set.py`, ADR-004/005/006 and the prospective timer/service
templates. No `.env` or API credentials were used for collection or research. No orders, runtime
changes, deployment, push or merge. Work remains as local commits on `research/recent-48h-perp5x`;
the original checkout and its in-flight rotation-capacity study were preserved.
