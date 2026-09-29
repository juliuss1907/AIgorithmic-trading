# Symbol-owned Spot 4h and Perp rule rollout

Registry key is `(symbol, scope)`. Schema v22 retains BTC's legacy Spot 1d rule and
position history, with that scope exit-only after cutover. New Spot uses Binance-native
UTC 4h candles for triggers; native 8h and 1d candles are context only. UI history is
shown in UTC+7, while stored timestamps, gates and candle boundaries remain UTC.

The worker idempotently bootstraps BTC, ETH, HYPE, NEAR, ZEC and SOL per scope. BTC
Perp keeps its existing champion. BTC/ETH have full lifecycle capability; the other
four assets are decision-soak-only and cannot reach paper. The current paper worker
executes BTC only; ETH paper execution is a separate rollout. No bootstrap, evaluation,
or LLM proposal automatically promotes a rule or enables paper.

## Spot 4h sequence

1. Backfill at least 365 days of closed native 4h/8h/1d Binance candles in pages of
   at most 1000. Missing or stale native context blocks new paper entries.
2. Replay Donchian 20/10 ATR14 on UTC 4h with walk-forward OOS evidence. Require at
   least 99% 4h coverage, six closed OOS trades, positive return after costs, drawdown
   below 8%, and no hard risk violation.
3. Operator starts decision-only soak using the exact passing replay evaluation ID.
   Require at least 14 days, 95% distinct 4h heartbeat coverage, six distinct setups
   with outcomes matured after three closed 4h bars, positive after-cost score, and
   no hard risk violation.
4. Operator activates using the exact latest passing soak evaluation ID. A separate
   paper activation remains necessary before fills.

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

This implementation is local only. VPS deployment, live soak observation, operator
activation, and paper canary are separate actions.
