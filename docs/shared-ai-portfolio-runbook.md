# Shared AI paper portfolio runbook

This runbook operates one 10,000 USDT parent paper portfolio. It allocates a 60% budget to
the daily BTC spot sleeve and 40% to the intraday BTC perpetual sleeve. The maximum target
inside each sleeve is 50%, so the parent caps are 30% spot and 20% perp notional. Perpetual
margin is isolated 3×. Parent hard limits are 50% gross exposure, ±50% BTC delta, 10%
isolated margin, −1.5% daily entry stop, and −8% drawdown flatten/halt.

The original promoted Donchian campaign in `lab/` is a separate immutable control. Do not
point these commands at its SQLite database or replace its timer.

## 1. Configure providers locally

Run the wizard twice. API keys are entered through a hidden prompt and are stored only in a
mode-0600 TOML file outside SQLite and Git.

```bash
aigt provider setup
# jev -> typesafe-systemone -> model jev-latest

aigt provider setup
# llm -> openai-compatible or anthropic-messages

aigt provider list
aigt doctor
```

The role matrix is enforced: TypeSafe/OpenRouter can only fill the Jev role;
OpenAI-compatible/Anthropic Messages can only fill the LLM role. `setup` runs a paid minimal
preflight and activates the profile only when it succeeds.

## 2. Run the decision-only soak

```bash
aigt portfolio soak run
```

This calls both `spot_daily_entry` and `perp_intraday_entry` workflows and records health
evidence. It does not call the paper ledger and cannot create a fill. In another terminal:

```bash
aigt serve
# open http://127.0.0.1:8081/portfolio
```

After at least 72 hours:

```bash
aigt portfolio soak evaluate
```

The result passes only with recent evidence from both scopes, at least 100 perp samples,
at least three spot samples, at least 95% availability per scope, and zero recorded hard-risk
violations. A pass still does not activate fills.

## 3. Manually activate paper fills

Copy the exact passing evaluation id:

```bash
aigt portfolio activate-paper --evaluation-id EVALUATION_ID
aigt portfolio status
aigt portfolio paper run
```

The paper worker fetches Binance public market data only. A spot entry requires both a causal
daily Donchian breakout and Jev approval. Perp entry requires the active bounded rule and Jev
direction. Donchian exits, perp stops, loss limits, and parent flattening execute without an
LLM/Jev response. The ledger refuses a same-tick perp flip.

Operator controls:

```bash
aigt portfolio pause
aigt portfolio resume
aigt portfolio flatten
aigt status
```

## 4. Export the immutable trade journal

The SQLite journal stores every returned Jev evaluation, including rejected gates. Each
signal retains the canonical state JSON prepared before the provider call, raw numeric
features, complete typed answers and probabilities, scoped champion id, and the applicable
LLM thesis. Provider failures have no answer to label and remain in `model_calls` instead.

Completed trades are inserted once when the round trip closes. The `signals` and `trades`
tables reject SQL updates and deletes; open positions are tracked separately. `is_paper=1`
for this release, while the field remains available to distinguish future live records.

```bash
aigt journal export \
  --min-pnl-pct 0.5 \
  --max-pnl-pct -0.5 \
  --output-dir training_data
```

The command writes `kev_finetune_YYYYMMDD.jsonl`. Profits above +0.5% preserve the original
entry direction, losses below −0.5% are labeled `hold`, and the inclusive range between the
thresholds is skipped. Values are percentage points, not decimal return ratios. Spot and perp
evaluations from one market tick remain separate examples because their serialized state
contains a different `decision_scope`.

## 5. Ubuntu VPS with Docker Compose

Keep the dashboard on loopback and access it through SSH:

```bash
cp .env.example .env.intraday
install -d -m 700 state/provider-secrets
install -m 600 /dev/null state/provider-secrets/provider-secrets.toml
docker compose --env-file .env.intraday -f deploy/intraday/compose.yaml up --build -d
ssh -L 8081:127.0.0.1:8081 USER@VPS
```

The default `PORTFOLIO_WORKER_MODE=soak`. After a passing evaluation and manual activation,
set it to `paper` and recreate only the worker:

```bash
docker compose --env-file .env.intraday -f deploy/intraday/compose.yaml \
  up -d --no-deps --force-recreate worker
```

Back up the named volume before upgrades. The build contains no live-order adapter and accepts
no Binance trading credentials.
