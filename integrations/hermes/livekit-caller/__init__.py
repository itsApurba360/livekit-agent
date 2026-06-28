# -*- coding: utf-8 -*-
"""Hermes plugin for triggering outbound calls through the LiveKit call-control API."""

import json
import os
import urllib.error
import urllib.parse
import urllib.request


MAKE_PHONE_CALL_SCHEMA = {
    "name": "make_phone_call",
    "description": (
        "Place an outbound phone call through the deployed LiveKit voice agent. "
        "Use this only when the user explicitly asks to call someone or after they confirm a call. "
        "Requires a phone number and a short purpose/reason for the call."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "phone_number": {
                "type": "string",
                "description": "Destination phone number, preferably E.164 such as +919876543210",
            },
            "purpose": {
                "type": "string",
                "description": "Short reason the voice agent should give for the call",
            },
            "agent_type": {
                "type": "string",
                "enum": ["sales", "support"],
                "description": "Optional persona override",
            },
            "customer_name": {
                "type": "string",
                "description": "Optional name to pass as context",
            },
            "company_name": {
                "type": "string",
                "description": "Optional company name to pass as context",
            },
        },
        "required": ["phone_number", "purpose"],
    },
}

GET_PHONE_CALL_STATUS_SCHEMA = {
    "name": "get_phone_call_status",
    "description": (
        "Fetch the latest status for an outbound phone call previously created by make_phone_call. "
        "Use the call_id returned by make_phone_call."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "call_id": {
                "type": "string",
                "description": "Call ID returned by make_phone_call, such as call_abc123def456",
            },
        },
        "required": ["call_id"],
    },
}


def _call_api_url() -> str:
    return os.environ.get("LIVEKIT_CALL_API_URL", "").rstrip("/")


def _call_api_token() -> str:
    return os.environ.get("LIVEKIT_CALL_API_TOKEN", "")


def make_phone_call(args: dict, **kwargs) -> str:
    del kwargs
    base_url = _call_api_url()
    token = _call_api_token()
    if not base_url or not token:
        return json.dumps(
            {
                "ok": False,
                "error": "LIVEKIT_CALL_API_URL and LIVEKIT_CALL_API_TOKEN must be configured in Hermes environment",
            }
        )

    payload = {
        "phone_number": args.get("phone_number"),
        "purpose": args.get("purpose"),
        "agent_type": args.get("agent_type"),
        "customer_name": args.get("customer_name"),
        "company_name": args.get("company_name"),
        "requested_by": "hermes",
        "metadata": {"source": "hermes-plugin"},
    }
    payload = {key: value for key, value in payload.items() if value is not None}
    data = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(
        f"{base_url}/calls",
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", errors="replace")
        return json.dumps({"ok": False, "status": err.code, "error": body})
    except Exception as err:
        return json.dumps({"ok": False, "error": str(err)})


def get_phone_call_status(args: dict, **kwargs) -> str:
    del kwargs
    base_url = _call_api_url()
    token = _call_api_token()
    if not base_url or not token:
        return json.dumps(
            {
                "ok": False,
                "error": "LIVEKIT_CALL_API_URL and LIVEKIT_CALL_API_TOKEN must be configured in Hermes environment",
            }
        )

    call_id = str(args.get("call_id") or "").strip()
    if not call_id:
        return json.dumps({"ok": False, "error": "call_id is required"})

    request = urllib.request.Request(
        f"{base_url}/calls/{urllib.parse.quote(call_id, safe='')}",
        method="GET",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", errors="replace")
        return json.dumps({"ok": False, "status": err.code, "error": body})
    except Exception as err:
        return json.dumps({"ok": False, "error": str(err)})


def register(ctx):
    ctx.register_tool(
        name="make_phone_call",
        toolset="livekit_caller",
        schema=MAKE_PHONE_CALL_SCHEMA,
        handler=make_phone_call,
        description="Place an outbound phone call through the deployed LiveKit voice agent",
    )
    ctx.register_tool(
        name="get_phone_call_status",
        toolset="livekit_caller",
        schema=GET_PHONE_CALL_STATUS_SCHEMA,
        handler=get_phone_call_status,
        description="Fetch the latest status of an outbound LiveKit phone call",
    )
