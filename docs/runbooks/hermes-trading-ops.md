# Runbook: Hermes trading operator

## Current state

The AIGT-side Operator API and the installable Hermes distribution are built, but no Hermes profile,
Telegram gateway, cron job, Unix user, or VPS service is installed by repository setup. This is
intentional: the repository is not yet deployed to the target VPS.

## Trust boundaries

- AIGT remains the trading and risk authority.
- Hermes reads only `http://127.0.0.1:8081/api/operator/v1/*`.
- The model-visible tool can only call `snapshot` and `no-trade`.
- Pause/resume are plugin slash commands and require a second `/trade_approve <request_id>` message.
- Flatten, provider changes, rule activation, database access, and live execution are unavailable.
- Use a dedicated Telegram bot and Unix user. Do not grant Docker socket, repo, SQLite, provider
  secret, `.env.intraday`, or dashboard control-token access.

## VPS prerequisites

1. Clone and install AIGT on the VPS, then confirm the paper worker and web service are healthy.
2. Install Hermes Agent `0.20.4` for the dedicated `hermes-trading` user.
3. Create two different random tokens. Put them in AIGT as
   `INTRADAY_OPERATOR_READ_TOKEN` and `INTRADAY_OPERATOR_ACTION_TOKEN`.
4. Keep `INTRADAY_OPERATOR_ACTIONS_ENABLED=false` for the first 72 hours.
5. Configure a dedicated Telegram bot and allow only the owner's Telegram user/chat ID.

## Profile installation

Run these commands only on the future VPS, as `hermes-trading`:

```bash
hermes profile install ./integrations/hermes/trading-ops --name trading-ops --alias --yes
trading-ops plugins doctor aigt_operator --ci
```

Populate the installed profile's `.env` with the loopback URL and tokens, mode `0600`. Configure the
Telegram platform so the owner appears in both `allow_from` and `allow_admin_from`; set regular-user
slash commands to the read-only `trade-status`, `trade-risk`, `trade-health`, `trade-experiment`,
`trade-rules`, and `trade-cost` set.

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
