# Intraday model-provider milestone

Implement secure CLI-managed provider profiles, paper-active Jev decisions, an hourly
structured LLM analyst pipeline, bounded rule candidates, redacted web operations, and
model-call telemetry. Provider credentials stay outside Git and SQLite. Jev failures
fall back to HOLD; LLM failures retain the last valid thesis and champion.

## Locked decisions

- Jev uses OpenRouter Decisions with pinned `typesafe/jev-1.13`.
- One OpenAI-compatible LLM profile serves all analyst roles.
- Activation applies atomically at the next five-second tick after preflight.
- Jev may affect paper decisions; LLM output remains behind replay and promotion gates.
- Current Binance, Hyperliquid, and verified RSS data only.
- Warn at USD 2 per UTC day; do not hard-stop model calls.
- Secret profile file is mode 0600 and never mounted into the web container.

## Delivery order

1. Contracts, additive SQLite migrations, and secure profile file.
2. Provider CLI, live preflight, assignments, and audit telemetry.
3. Jev adapter and paper-runtime integration.
4. Structured LLM reports, thesis, bounded rule candidate, and scheduler.
5. Provider/analyst web operations, Docker wiring, docs, and end-to-end verification.
