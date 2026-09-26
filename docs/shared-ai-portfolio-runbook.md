# Shared AI paper portfolio runbook

This runbook operates one 10,000 USDT parent paper portfolio. It allocates a 60% budget to
the daily BTC spot sleeve and 40% to the intraday BTC perpetual sleeve. The maximum target
inside each sleeve is 50%, so the parent caps are 30% spot and 20% perp notional. Perpetual
margin is isolated 3×. Parent hard limits are 50% gross exposure, ±50% BTC delta, 10%
isolated margin, −1.5% daily entry stop, and −8% drawdown flatten/halt.

The original promoted Donchian campaign in `lab/` is a separate immutable control. Do not
point these commands at its SQLite database or replace its timer.

## 1. Configure providers locally

Run the two connection wizards. Each typed API-key character is shown as `*`; the key is
stored only in a mode-0600 TOML file outside SQLite and Git.

```bash
aigt connect jev
# OpenRouter / TypeSafe / custom System One-compatible provider

aigt connect llm
# Anthropic-compatible / OpenAI-compatible provider

aigt provider list
aigt doctor
```

Use `↑`/`↓` to move, `Enter` to select, and `Ctrl+C` to cancel either menu.
The command fixes the role before provider selection, so Jev and LLM protocols cannot be
mixed. `connect` runs a paid minimal preflight and activates the generated profile only
when it succeeds. A failed preflight is not persisted and does not replace the active
provider.

## 2. Verify configuration and cadence health

The default operating contract is deliberately multi-cadence:

| Work | Cadence | May call a model? |
|---|---:|---|
| Deterministic risk and mark-to-market | 5 seconds | No |
| Binance order book refresh | 15 seconds | No |
| Perp numeric Jev decision | 30 seconds | Jev |
| Funding, OI and long/short refresh | 60 seconds | No |
| Compact Jev shadow pair | 15 minutes | Jev, observation only |
| News ingest | 30 minutes | No; scripts ingest and normalize feeds |
| Evidence-aware market thesis | 60 minutes, only when evidence changes | LLM |
| Retrospective and rule proposal | 09:00 Asia/Ho_Chi_Minh | Deterministic review, then LLM proposal |
| Spot daily decision | New closed daily candle and eligible Donchian setup | Jev |

Confirm the effective values before starting a worker:

```bash
aigt doctor
```

The output includes the schema version, configured cadence values, active provider profiles,
and the latest durable scheduler record for each job. `execution_enabled` remains `false` in
this release.

## 3. Run the decision-only soak

```bash
aigt portfolio soak run
```

This records decision-health evidence without calling the paper ledger and cannot create a
fill. Perp decisions follow the 30-second cadence. Spot does not call Jev every loop; it is
event-driven by a newly closed daily candle and an eligible Donchian setup. In another terminal:

```bash
aigt serve
# open http://127.0.0.1:8081/portfolio
```

Có thể lấy một snapshot readiness read-only bất kỳ lúc nào mà không ghi evaluation:

```bash
aigt portfolio soak report
aigt portfolio soak report --output ./soak-readiness.json
```

Report gồm tiến độ/thời điểm soak, sample và availability theo scope, heartbeat,
hard-risk violations, provider/scheduler health, model cost, safety state, fill/trade
counts, SQLite integrity/dung lượng và evaluation đã persist gần nhất. File output có
quyền `0600`, được publish atomic và không ghi đè. `recommended_action` chỉ mô tả bước
tiếp theo; lệnh này không thực thi action và trả exit code 0 cho report `deferred`,
`reject` hoặc degraded nếu việc tạo report vẫn thành công.

After at least 72 hours:

```bash
aigt portfolio soak evaluate
```

The result passes only with recent evidence from both scopes, at least 100 perp samples,
at least three spot samples, at least 95% availability per scope, and zero recorded hard-risk
violations. Chỉ `soak evaluate` mới persist evaluation; một pass vẫn không activate fills.

## 4. Manually activate paper fills

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
aigt positions
aigt portfolio pause
aigt portfolio resume
aigt portfolio flatten
aigt status
```

`aigt positions` là read-only và chỉ liệt kê sleeve đang mở. `quantity` dương là
long, quantity âm là short; `mark_event_time` cho biết timestamp của market snapshot
được dùng để tính `notional_usd` và `unrealized_pnl_usd`. Kết quả rỗng được biểu diễn
bằng `count: 0` và `positions: []`.

## 5. Review learning evidence and rule candidates

Open `http://127.0.0.1:8081/` for the unified operations view: current worker
status, model-backed 72-hour soak progress, Spot/Perp heartbeats, safe Jev signal
summaries, provider health, market references, and the latest thesis. Open
`http://127.0.0.1:8081/portfolio` for the detailed portfolio view and inspect:

- latest durable scheduler status and errors;
- numeric-primary versus compact-shadow pairs and the current eligibility result;
- forward-outcome coverage for Perp 15-minute and Spot 3-day labels;
- the latest 09:00 retrospective, model-call telemetry and UTC daily cost;
- scoped champion, challenger and rollback rule IDs.

The same operational snapshot is read-only at `/api/operations`. CLI equivalents are:

```bash
aigt portfolio experiment status
aigt portfolio experiment evaluate --scope all
aigt portfolio retrospective run --once
aigt portfolio rules status
```

Compact state remains shadow-only. It cannot be eligible until it has at least 14 days,
1,000 paired samples, 95% availability, no more than a 2 percentage-point accuracy regression,
no more than a 0.02 Brier regression, and at least 15% input-token reduction. Eligibility never
activates it automatically.

Daily LLM rule proposals enter a separate lifecycle for each scope. The operator must run:

```bash
aigt portfolio rules replay CANDIDATE_ID
aigt portfolio rules start-soak CANDIDATE_ID
# wait at least 72 hours while the paper worker records decision-only comparisons
aigt portfolio rules evaluate CANDIDATE_ID
aigt portfolio rules activate CANDIDATE_ID --evaluation-id PASSING_EVALUATION_ID
```

Activation accepts only the exact latest passing evaluation ID. A challenger never creates a
fill while it is soaking.

## 6. Export the immutable trade journal

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

## 7. Back up and verify SQLite state

Create a consistent online SQLite copy without stopping or restarting the worker:

```bash
aigt backup create
```

The global command uses a one-off `admin --no-deps` container, reads the live named volume with
SQLite's online backup API, and writes the artifact outside that volume under
`${XDG_STATE_HOME:-~/.local/state}/aigorithmic-trading/backups`. It publishes a mode-0600
`.sqlite3` file only after `PRAGMA integrity_check` succeeds, plus a mode-0600 JSON manifest with
the byte count and SHA-256 checksum. Verify the exact path returned by `create`:

```bash
aigt backup verify /absolute/path/to/intraday-TIMESTAMP.sqlite3
```

Copy both the database and its sibling `.manifest.json` to separate storage. This MVP does not
schedule backups, prune old artifacts, restore state, encrypt files, or upload off-host. Provider
secrets are deliberately excluded. Use `--output-dir` for another host directory and
`--database` only for a native database that is not inside the registered Docker deployment.

## 8. Ubuntu VPS with Docker Compose

Install the global CLI from the clone, then let it register and manage the Docker deployment:

```bash
uv tool install --editable .
uv tool update-shell
aigt setup
aigt doctor
ssh -L 8081:127.0.0.1:8081 USER@VPS
```

After setup, `aigt start`, `stop`, `restart`, `logs`, `status`, `doctor`, and `provider
setup` work from any directory. `aigt setup --with-hermes` installs the optional read-only
operator profile but never starts its gateway or cron jobs.

The default `PORTFOLIO_WORKER_MODE=soak`. After a passing evaluation and manual activation,
set it to `paper` and recreate only the worker. The raw Compose form below is retained for
troubleshooting; routine service control uses `aigt restart`:

```bash
docker compose --env-file .env.intraday -f deploy/intraday/compose.yaml \
  up -d --no-deps --force-recreate worker
```

Run and verify `aigt backup create` before upgrades. The build contains no live-order adapter and
accepts no Binance trading credentials.

## 9. Evidence windows are operational work, not build completion

Passing the automated tests proves contracts and deterministic behavior; it does not create a
track record. Before considering any broader deployment, keep the worker and dashboard running,
complete the initial 72-hour soak, then collect at least 14 days of numeric/compact evidence.
Review provider errors, scheduler gaps, outcome coverage, costs, and rule evaluations manually.
