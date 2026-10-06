# Setup 2 descriptive diagnostics

This command reads the existing Setup-2 comparison and journals. It does not run
the strategy, fetch new market data, change exceptions, or select a filter.
The existing window is [2022-01-01, 2026-10-02) UTC: SOL/ZEC/NEAR 4:3:3,
ADX20 increasing plus directional DMI, Spot60% / Short40%, initial1,000USDT.
Realized reinvestment remains pooled within each market, with no capital reset.

Run from the committed diagnostic checkout with its existing Python environment:

```bash
.venv/bin/python -m scripts.diagnose_donchian_setup2 \
  --comparison /home/julius/.local/state/aigorithmic-trading/reports/donchian-adx-setups-20261005/study-01/runs/comparison.json \
  --inventory /home/julius/julius-workspace/ai-labs/projects/system-trading/state/research-reports/donchian-adx-summary-20261006/all-setups.json \
  --report-root /home/julius/julius-workspace/ai-labs/projects/system-trading/state/research-reports/donchian-setup2-diagnostics-20261006
```

An output root must not already exist. Files are private, written exclusively;
preflight rejects a dirty checkout or tampered source. `binding.json` and
`grouping-config.json` freeze source/config/journal hashes, analysis commit, and
all rules before trade outcomes are joined or aggregated. The full source copy,
Markdown, JSON, trade CSV, group CSV, plots, verification, and manifest are saved.
An interrupted run leaves its binding/evidence intact; resume requires a fresh
root rather than overwriting it.

Each entry key (UTC time, market, symbol) must be unique and causal. Initial risk
is quantity times the absolute distance from fill entry price to the initial
entry-feature stop. The stop must equal entry price minus/plus3ATR. Net R includes
fees, slippage, and signed funding; the risk denominator excludes those costs.
Negative funding paid is a credit. R sums are descriptive and not equity returns.

Five one-dimensional variables use predeclared left-open, right-closed bins:

| Variable | Bins |
|---|---|
| ADX | (20,25], (25,35], >35 |
| ADX minus previous H4 ADX | (0,1], (1,3], >3 |
| ATR / signal H4 close ×100 | (0,2], (2,4], >4 |
| Volume / VolumeMA20 | (1.2,2], (2,3], >3 |
| side × (signal H4 close − EMA50) / ATR | (0,1], (1,2], >2 |

Side is +1 for Spot longs, −1 for Perp shorts. All bins apply at portfolio scope
and each of six coin/market scopes. No variable combinations are scanned. Empty
bins remain visible. Groups overlap; group PnLs must not be added across scopes
or variables. Global top10 overlap always refers to the same USDT-ranked winners.

Review eligibility is frozen: at least20 trades and negative overall mean net R,
plus negative mean net R in at least3 complete entry years2022–2025, each with
at least5 trades. Partial2026 cannot qualify. Under20 is low sample. No eligible
groups means no candidate; rules are not relaxed after reading outcomes.

Years and half-years attribute the full closed trade result to its opening
period, not marked equity change during that period.2026 and2026-H2 are partial;
2026-H1 is complete. Each of112 groups exports overall,5 yearly,10 half-year
rows, totalling1,792 CSV rows. Trade CSV has419 rows and exact Decimal values.

Top1/5/10/20 positive winners are ranked separately by net USDT and net R.
Remainder sums describe the fixed journal; they do not simulate deleting trades,
which would change reinvestment, sizing, signals, and guard state. These are
seen-data hypotheses, not fresh OOS evidence or trading approval.

Verification reconciles419 trades,177 winners,242 losers,663.9825162550667USDT
net and174.51370959896636USDT costs, including original six market contributions.
The diagnostic computation is repeated with reversed input order and must match
all normalized rows and analysis output exactly. Input files, all18 published
reports, engine files, and diagnostic source are hashed again before completion.

```bash
.venv/bin/python -m pytest -q tests/test_donchian_setup2_diagnostics.py
git diff --check
```

Scope: local research and local commits only; no strategy modification, push,
merge, deployment, LLM invocation, or trading activation.
