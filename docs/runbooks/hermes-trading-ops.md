# Runbook: Hermes trading operator

## Current state

Hermes is an optional operator add-on. `aigt setup` always boots the AIGT core without Hermes;
`aigt setup --with-hermes` additionally installs the read-only `trading-ops` profile. Setup never
starts a Hermes gateway, configures Telegram, creates cron jobs, or enables actions.

## Trust boundaries

- AIGT remains the trading and risk authority.
- Hermes reads only `http://127.0.0.1:8081/api/operator/v1/*`.
- The model-visible tool can only call `snapshot` and `no-trade`.
- Pause/resume are plugin slash commands and require a second `/trade_approve <request_id>` message.
- Flatten, provider changes, rule activation, database access, and live execution are unavailable.
- Use a dedicated Telegram bot. A shared Unix user is acceptable only for the initial read-only
  soak because the profile exposes only `aigt-operator`; move it to a dedicated user before
  enabling actions. Never expose Docker, SQLite, provider secrets or the dashboard control token
  to the model.

## VPS prerequisites

1. Install global `aigt` from the clone with `uv tool install --editable .`.
2. Run `aigt setup --with-hermes`; Docker/Compose are required, Hermes itself is optional.
3. Confirm `aigt doctor`, `aigt status`, and `hermes -p trading-ops plugins doctor
   aigt_operator --ci` pass.
4. Keep `INTRADAY_OPERATOR_ACTIONS_ENABLED=false` and the Hermes action token empty.
5. Configure a dedicated Telegram bot and allow only the owner's Telegram user/chat ID.

## Profile installation

Normal installation is one command from the repository root:

```bash
aigt setup --with-hermes
```

Setup creates the profile `.env` with the loopback URL and read credential at mode `0600`; the
action credential remains empty. Configure Telegram separately so the owner appears in both
`allow_from` and `allow_admin_from`; set regular-user slash commands to the read-only
`trade-status`, `trade-risk`, `trade-health`, `trade-experiment`, `trade-rules`, and `trade-cost`
set.

Register cron after Telegram delivery works. `02:05 UTC` is `09:05 Asia/Ho_Chi_Minh` year-round:

```bash
trading-ops cron create "every 60s" \
  --name aigt-critical-alerts \
  --script "$HERMES_HOME/scripts/aigt_alert_watch.py" \
  --no-agent --deliver telegram

trading-ops cron create "5 2 * * *" \
  "Write a concise Vietnamese daily paper-trading report. Use only the attached snapshot; state unknown fields explicitly and perform no mutations." \
  --name aigt-daily-digest \
  --script "$HERMES_HOME/scripts/aigt_daily_context.py" \
  --skill aigt-operator --deliver telegram
```

Install the Hermes gateway as a boot service only after the profile passes the checks above.

## Acceptance sequence

1. `/trade_status`, `/trade_risk`, and a natural-language no-trade question match AIGT dashboard/CLI.
2. Alert watcher stays silent with no new events and sends each recorded event once after restart.
3. Run read-only for 72 hours with `INTRADAY_OPERATOR_ACTIONS_ENABLED=false`.
4. Enable actions, restart only the AIGT web service, and test pause on paper mode.
5. Confirm request creation does not queue a command until the exact request ID is approved.
6. Confirm approval replay, expiry, cancellation, and hard-risk resume are rejected.

If Hermes is unavailable, do not restart or pause AIGT merely to restore notifications. Trading and
risk loops are designed to continue independently.
