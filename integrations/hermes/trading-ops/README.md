# Hermes trading-ops distribution

This directory is an installable Hermes profile distribution. It is version-controlled with AIGT
but remains inactive until explicitly installed on the VPS.

It contains:

- A read-only model tool for the redacted Operator API.
- Deterministic slash commands for status and two-step pause/resume.
- A no-agent critical alert watcher.
- A bounded daily-digest context script.

Do not put credentials in this directory. `hermes profile install` converts `.env.template` into
`.env.EXAMPLE`; the real profile `.env` remains user-owned and is never updated by the distribution.

Deployment is intentionally deferred until the repository and Hermes are both present on the VPS.
Follow `docs/runbooks/hermes-trading-ops.md` at that time.
