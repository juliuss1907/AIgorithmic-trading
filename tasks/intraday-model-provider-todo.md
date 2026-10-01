# Intraday model-provider checklist

> Provider-module implementation history. Current cadence/Spot-Perp lifecycle/execution
> boundaries: [canonical architecture](../docs/crypto-intraday-system-design.md).
> These build checks do not establish live provider health or trading activation.


- [x] Add provider/report contracts and additive SQLite migrations.
- [x] Add atomic 0600 secret profile store and CLI CRUD.
- [x] Add provider preflight, activation, redacted metadata, and model-call audit.
- [x] Add OpenRouter Jev Decisions adapter with fail-closed behavior.
- [x] Wire active Jev into the long-running paper worker.
- [x] Add structured five-stage LLM analysis pipeline and hourly scheduling.
- [x] Add bounded candidate generation and deferred replay/promotion lifecycle.
- [x] Add authenticated provider/analyst dashboard operations.
- [x] Add Docker secret mounts, runbook, browser checks, and full verification.
