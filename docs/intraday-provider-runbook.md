# Intraday model-provider runbook

## Safety boundary

This system is paper-only. Provider activation can change Jev paper decisions and can
produce LLM rule candidates, but it cannot place an exchange order. Isolated leverage
stays fixed at 3× and deterministic risk checks remain authoritative.

Credentials live only in the provider TOML file. SQLite, dashboard responses, command
payloads, model-call telemetry, and logs contain redacted metadata or hashes. The file
must be regular (not a symlink), mode `0600`, and owned by the current user when the
process is not root.

## Local setup

Install the repository as an editable global tool once:

```bash
uv sync --frozen
uv tool install --editable .
uv tool update-shell
aigt --version
```

The default paper database is
`${XDG_STATE_HOME:-~/.local/state}/aigorithmic-trading/intraday.sqlite3`. To retain the
legacy repository-local account, copy it once with SQLite integrity checks:

```bash
aigt migrate-state --from state/intraday/intraday.sqlite3
```

The migration never removes its source and refuses to overwrite an existing target.
Stop the old worker before migrating, then use `aigt` for subsequent runs.

Use the interactive connection commands for normal setup. The API-key prompt displays
one `*` per typed character while keeping the key out of logs and shell history:

```bash
aigt connect jev
# OpenRouter / TypeSafe / custom System One-compatible provider

aigt connect llm
# Anthropic-compatible / OpenAI-compatible provider
```

Use `↑`/`↓` to move through either menu, `Enter` to select, and `Ctrl+C` to cancel.
The Jev OpenRouter and TypeSafe choices supply their official endpoints and suggested
models. Custom Jev, Anthropic-compatible, and OpenAI-compatible choices ask for the
provider URL and model ID. A minimal paid preflight runs before anything is saved; a
failed connection leaves the current active provider unchanged.
The normal connection flow prints only `provider connected`, `API error`, or
`Invalid url`; inspect redacted metadata later with `aigt provider list`.

For automation, `--api-key-stdin` reads exactly one line. Do not put a key in a command
argument, `.env`, shell history, issue, or log. The advanced `provider add`, `test`, and
`activate` commands remain available for non-interactive workflows.

Each profile must pass a live preflight before activation:

```bash
aigt provider test jev-openrouter
aigt provider activate jev jev-openrouter
aigt provider test llm-main
aigt provider activate llm llm-main
aigt provider list
aigt doctor
```

Preflight success expires after 10 minutes. Jev activation is observed atomically at
the next five-second tick. LLM activation is captured once at the beginning of an
hourly analysis cycle, so all five roles use the same profile and model.

To rotate a key, run `provider add ... --replace`, preflight the new fingerprint, then
activate it. To stop model use without deleting metadata:

```bash
aigt provider deactivate jev
aigt provider deactivate llm
```

An active profile cannot be removed. Deactivate it first, then use `provider remove`.

## Docker setup

Create the bind-mounted directory and empty file before Compose starts:

```bash
install -d -m 700 state/provider-secrets
install -m 600 /dev/null state/provider-secrets/provider-secrets.toml
cp .env.example .env.intraday
```

Use the `admin` profile for credential writes and provider metadata changes. It mounts
the secret directory read-write and the same SQLite volume as the worker:

```bash
docker compose --env-file .env.intraday -f deploy/intraday/compose.yaml \
  --profile admin run --rm admin \
  provider add jev-openrouter --role jev --kind openrouter-decisions \
  --model typesafe/jev-1.13 --database /app/state/intraday/intraday.sqlite3 \
  --secrets-file /run/provider-secrets/provider-secrets.toml
```

Run `provider test` and `provider activate` with the same prefix. The long-running
worker mounts `/run/provider-secrets` read-only. The web container does not mount that
directory at all.

## Dashboard operations

Read-only endpoints require no control credential on the loopback-only dashboard:

- `GET /api/providers` — profiles, assignments, preflight status, no secret presence.
- `GET /api/analysts` — latest three reports, thesis, and UTC-day model cost.

Mutations require `Authorization: Bearer <INTRADAY_CONTROL_TOKEN>` and a unique
`Idempotency-Key`. They enqueue commands; the web process never calls a provider or
opens the secret file.

```bash
curl -X POST http://127.0.0.1:8081/api/providers/jev-openrouter/tests \
  -H "Authorization: Bearer $INTRADAY_CONTROL_TOKEN" \
  -H "Idempotency-Key: jev-test-20260922-1"

curl -X PUT http://127.0.0.1:8081/api/provider-assignments/jev \
  -H "Authorization: Bearer $INTRADAY_CONTROL_TOKEN" \
  -H "Idempotency-Key: jev-activate-20260922-1" \
  -H "Content-Type: application/json" \
  -d '{"profile_id":"jev-openrouter"}'
```

Do not paste a real control token into shared shell history. Prefer an SSH session with
a temporary environment variable or invoke provider commands through the local CLI.

## Expected failure behavior

- Jev timeout, malformed output, HTTP error, or open circuit → audited fallback
  `Hold`; no same-tick retry and no new paper exposure.
- Three consecutive Jev failures → circuit opens for 60 seconds, then one half-open
  attempt is allowed.
- LLM stage failure → the cycle is degraded; the last valid thesis and champion remain.
- Candidate without 90 days of sufficiently complete replay evidence → `deferred`.
- Replay-passed challenger with fewer than 14 days or 30 closed trades → `deferred`.
- UTC-day model cost above USD 2 → dashboard/runtime warning only; calls are not
  automatically stopped.

Inspect redacted status with:

```bash
aigt doctor
aigt provider list
aigt analysis
```

## Protocol references

- [OpenRouter Jev compiler and Decisions example](https://openrouter.ai/labs/jev/compile)
- [OpenRouter AI SDK provider Decisions contract](https://github.com/OpenRouterTeam/ai-sdk-provider#evaluation-jev-with-ai-sdk-through-openrouter)
- [OpenRouter OpenAI-compatible quickstart](https://openrouter.ai/docs/quickstart)
- [OpenRouter structured outputs](https://openrouter.ai/docs/guides/features/structured-outputs)
