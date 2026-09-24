"""Hermes plugin exposing read-only model access and deterministic commands."""

from __future__ import annotations

import json
import secrets

from .client import OperatorClient, OperatorClientError


READ_SCHEMA = {
    "name": "aigt_operator_read",
    "description": "Read the redacted AIGT paper-trading operator projection.",
    "parameters": {
        "type": "object",
        "properties": {
            "view": {"type": "string", "enum": ["snapshot", "no_trade"]},
            "scope": {
                "type": "string",
                "enum": ["spot_daily", "perp_intraday"],
            },
            "window_minutes": {"type": "integer", "minimum": 5, "maximum": 1440},
        },
        "required": ["view"],
        "additionalProperties": False,
    },
}


def _client() -> OperatorClient:
    return OperatorClient.from_environment()


def _safe_error(error: Exception) -> str:
    code = error.code if isinstance(error, OperatorClientError) else "invalid_request"
    return f"AIGT operator request failed ({code})."


def _read_tool(args: dict, **_) -> str:
    try:
        view = args.get("view")
        if view == "snapshot":
            payload = _client().snapshot()
        elif view == "no_trade":
            payload = _client().no_trade(
                str(args.get("scope") or ""), int(args.get("window_minutes") or 60)
            )
        else:
            raise ValueError("unsupported view")
        return json.dumps({"ok": True, "data": payload}, sort_keys=True)
    except (OperatorClientError, ValueError, TypeError) as error:
        return json.dumps({"ok": False, "error": _safe_error(error)}, sort_keys=True)


def _snapshot_section(section: str) -> str:
    try:
        payload = _client().snapshot()
        value = payload if section == "all" else payload.get(section)
        return json.dumps(
            {"generated_at": payload.get("generated_at"), section: value},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    except (OperatorClientError, ValueError) as error:
        return _safe_error(error)


def _request_action(action: str, raw_args: str) -> str:
    if raw_args.strip():
        return f"Usage: /trade_{action}"
    try:
        item = _client().create_action(
            action, idempotency_key=f"telegram-{secrets.token_hex(12)}"
        )
    except (OperatorClientError, ValueError) as error:
        return _safe_error(error)
    return (
        f"Yêu cầu {action} đang chờ xác nhận.\n"
        f"Request ID: {item['id']}\n"
        f"Hết hạn: {item['expires_at']}\n"
        f"Xác nhận bằng: /trade_approve {item['id']}"
    )


def _approve(raw_args: str) -> str:
    request_id = raw_args.strip()
    if not request_id:
        return "Usage: /trade_approve <request_id>"
    try:
        item = _client().approve(request_id)
    except (OperatorClientError, ValueError) as error:
        return _safe_error(error)
    return f"Đã xác nhận {item['action']}; command đang ở trạng thái {item['status']}."


def _cancel(raw_args: str) -> str:
    request_id = raw_args.strip()
    if not request_id:
        return "Usage: /trade_cancel <request_id>"
    try:
        item = _client().cancel(request_id)
    except (OperatorClientError, ValueError) as error:
        return _safe_error(error)
    return f"Đã hủy yêu cầu {item['id']}."


def register(ctx) -> None:
    ctx.register_tool(
        name="aigt_operator_read",
        toolset="aigt-operator",
        schema=READ_SCHEMA,
        handler=_read_tool,
        description="Read redacted AIGT paper-trading state; never mutates trading state.",
        emoji="📈",
    )
    for name, section in {
        "trade-status": "all",
        "trade-risk": "risk",
        "trade-health": "health",
        "trade-experiment": "experiments",
        "trade-rules": "rules",
        "trade-cost": "costs",
    }.items():
        ctx.register_command(
            name,
            handler=lambda _args, selected=section: _snapshot_section(selected),
            description=f"Read AIGT {section} status without an LLM call.",
        )
    ctx.register_command(
        "trade-pause",
        handler=lambda args: _request_action("pause", args),
        description="Create a pending AIGT pause request.",
    )
    ctx.register_command(
        "trade-resume",
        handler=lambda args: _request_action("resume", args),
        description="Create a pending AIGT resume request.",
    )
    ctx.register_command(
        "trade-approve",
        handler=_approve,
        description="Approve one exact pending AIGT action request.",
        args_hint="<request_id>",
    )
    ctx.register_command(
        "trade-cancel",
        handler=_cancel,
        description="Cancel one exact pending AIGT action request.",
        args_hint="<request_id>",
    )
