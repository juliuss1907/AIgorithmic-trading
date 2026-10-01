# Symbol-owned Spot 4h and Perp rule rollout

Registry key is `(symbol, scope)`. Source schema v23 retains BTC's legacy Spot 1d rule and
position history, with that scope exit-only after cutover. New Spot uses Binance-native
UTC 4h candles for triggers; native 8h and 1d candles are context only. UI history is
shown in UTC+7, while stored timestamps, gates and candle boundaries remain UTC.

The worker idempotently bootstraps registered catalog symbols per enabled scope.
BTC Perp keeps its existing champion. Source v23 removes the ticker capability gate;
every symbol still needs its own lifecycle, rule and evidence. The legacy parent
simulator executes BTC only; multi-route Demo execution supports registered/supported
coins after separate activation. No bootstrap, evaluation, or LLM proposal promotes
a rule or enables execution automatically.

## Spot 4h sequence

1. Backfill at least 365 days of closed native 4h/8h/1d Binance candles in pages of
   at most 1000. Missing or stale native context blocks new paper entries.
2. Replay the Donchian 20/10 baseline on UTC 4h with walk-forward OOS evidence. The
   rule carries ATR14 sizing, but the current replay does not simulate that sizing
   or full Demo risk; fixed costs are 0.15% per side. Require at
   least 99% 4h coverage, six closed OOS trades, positive return after costs, drawdown
   below 8%, and no hard risk violation.
3. Operator starts decision-only soak using the exact passing replay evaluation ID.
   Require at least 14 days, 95% distinct 4h heartbeat coverage, six distinct setups
   with outcomes matured after three closed 4h bars, positive after-cost score, and
   no hard risk violation.
4. Operator promotes using the exact latest passing soak evaluation ID. A separate
   execution activation remains necessary before fills (legacy simulator or Demo).

## Perp sequence

1. Start deterministic baseline decision soak before replay. Require at least 14 days,
   100 matured 15m outcomes, 95% outcome and 30s heartbeat coverage.
2. Replay only pre-cutoff evidence, then validate on disjoint post-replay evidence for
   at least 72 hours, 100 outcomes and 95% coverage. Scores must be positive after
   costs, with no hard risk violation.
3. Operator activates by exact latest passing post-replay evaluation ID. BTC Perp
   continues under its existing champion while Spot 4h is being prepared.

After a rejected candidate or deteriorated champion, the worker may ask the LLM for
one bounded per-symbol/scope proposal only after fresh evidence. The limit is three
calls per 90 days and one open candidate. Spot 4h proposals tune Donchian entry/exit
windows only. All candidates must pass the same evidence gates and manual activation.

Source v23 worker/web were deployed at the recorded 2026-10-01 05:30 UTC checkpoint;
this is a dated snapshot, not live status. Operator promotion, Demo acceptance and
execution activation remain separate. Perp minimum is 14 days before replay plus
72 hours of independent post-replay validation, not 14 days total.

Execution modules and current rollout boundaries: [canonical architecture](../docs/crypto-intraday-system-design.md).
Legacy BTC parent paper remains independent; Spot replay does not yet model full Demo risk.
