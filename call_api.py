# -*- coding: utf-8 -*-
import json
import os
import re
import uuid
from collections import Counter
from typing import Any, Optional

from call_dashboard import dashboard_html
from call_outcomes import failure_status_for_reason, sip_failure_reason
from call_status_store import create_call_record, get_call_record, list_call_records, now_iso, update_call_record
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from livekit import api
from pydantic import BaseModel, Field, field_validator

load_dotenv()

AGENT_NAME = os.environ.get("LIVEKIT_AGENT_NAME") or os.environ.get("AGENT_NAME") or "outbound-caller"

app = FastAPI(title="LiveKit Call Control API", version="0.1.0")


def _call_api_token() -> str:
    return os.environ.get("CALL_API_TOKEN", "").strip()


def _allowed_country_prefixes() -> list[str]:
    raw = os.environ.get("CALL_API_ALLOWED_COUNTRY_PREFIXES", "+91")
    return [item.strip() for item in raw.split(",") if item.strip()]


def _default_country_code() -> str:
    return os.environ.get("CALL_API_DEFAULT_COUNTRY_CODE", "+91").strip()


def _max_purpose_chars() -> int:
    try:
        return int(os.environ.get("CALL_API_MAX_PURPOSE_CHARS", "300"))
    except ValueError:
        return 300


def _dashboard_summary(calls: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts = Counter(call.get("status") or "unknown" for call in calls)
    connected_statuses = {"answered", "active", "completed"}
    live_statuses = {"dispatching", "dispatched", "dialing"}
    return {
        "total": len(calls),
        "live": sum(1 for call in calls if call.get("status") in live_statuses),
        "connected": sum(1 for call in calls if call.get("status") in connected_statuses),
        "failures": sum(
            1
            for call in calls
            if str(call.get("status") or "").startswith("failed") or call.get("status") == "dispatch_failed"
        ),
        "busy": sum(1 for call in calls if call.get("reason") == "busy" or call.get("status") == "failed_busy"),
        "status_counts": dict(status_counts),
        "generated_at": now_iso(),
    }


def _require_auth(authorization: Optional[str] = None) -> None:
    expected_token = _call_api_token()
    if not expected_token:
        raise HTTPException(status_code=503, detail="CALL_API_TOKEN is not configured")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    supplied_token = authorization.removeprefix("Bearer ").strip()
    if supplied_token != expected_token:
        raise HTTPException(status_code=403, detail="Invalid bearer token")


def normalize_phone_number(raw_phone: str) -> str:
    phone = (raw_phone or "").strip()
    if not phone:
        raise HTTPException(status_code=422, detail="phone_number is required")

    cleaned = re.sub(r"[^0-9+]", "", phone)
    if cleaned.count("+") > 1 or ("+" in cleaned and not cleaned.startswith("+")):
        raise HTTPException(status_code=422, detail="Invalid phone number format")

    if not cleaned.startswith("+"):
        cleaned = f"{_default_country_code()}{cleaned}"

    if not re.fullmatch(r"\+[1-9]\d{9,14}", cleaned):
        raise HTTPException(status_code=422, detail="Invalid E.164 phone number")

    allowed_prefixes = _allowed_country_prefixes()
    if allowed_prefixes and not any(cleaned.startswith(prefix) for prefix in allowed_prefixes):
        raise HTTPException(status_code=422, detail=f"Phone number prefix is not allowed: {cleaned}")

    return cleaned


class CallRequest(BaseModel):
    phone_number: str = Field(..., description="Destination phone number, preferably E.164")
    purpose: str = Field(..., description="Short reason the agent should give for the call")
    agent_type: Optional[str] = Field(default=None, description="Optional override: support or sales")
    customer_name: Optional[str] = None
    company_name: Optional[str] = None
    requested_by: str = "hermes"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("purpose")
    @classmethod
    def validate_purpose(cls, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise ValueError("purpose is required")
        if len(value) > _max_purpose_chars():
            raise ValueError("purpose is too long")
        return value

    @field_validator("agent_type")
    @classmethod
    def validate_agent_type(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip().lower()
        if normalized not in {"support", "sales"}:
            raise ValueError("agent_type must be support or sales")
        return normalized


class CallResponse(BaseModel):
    ok: bool
    call_id: str
    room_name: str
    status: str
    phone_number: str
    reason: Optional[str] = None
    sip_status_code: Optional[str] = None
    sip_status: Optional[str] = None
    sip_call_id: Optional[str] = None
    error: Optional[str] = None


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard() -> HTMLResponse:
    return HTMLResponse(dashboard_html())


@app.get("/dashboard/data")
def dashboard_data(
    limit: int = Query(default=100, ge=1, le=500),
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _require_auth(authorization)
    calls = list_call_records(limit=limit)
    return {
        "ok": True,
        "summary": _dashboard_summary(calls),
        "calls": calls,
    }


async def _create_room_and_dispatch_agent(room_name: str, dispatch_metadata: dict[str, Any]) -> Any:
    """Create the LiveKit room and dispatch the configured worker into it."""
    metadata_json = json.dumps(dispatch_metadata, ensure_ascii=False)
    async with api.LiveKitAPI() as lk:
        await lk.room.create_room(api.CreateRoomRequest(name=room_name, metadata=metadata_json))
        return await lk.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name=AGENT_NAME,
                room=room_name,
                metadata=metadata_json,
            )
        )


_dispatch_livekit_agent = _create_room_and_dispatch_agent


def _outbound_trunk_id(config_dict: Optional[dict[str, Any]] = None) -> Optional[str]:
    config_dict = config_dict or {}
    return (
        config_dict.get("outbound_trunk_id")
        or config_dict.get("sip_trunk_id")
        or os.environ.get("OUTBOUND_TRUNK_ID")
        or os.environ.get("LIVEKIT_OUTBOUND_TRUNK_ID")
        or os.environ.get("SIP_OUTBOUND_TRUNK_ID")
    )


async def _create_outbound_sip_participant(
    *,
    room_name: str,
    phone_number: str,
    participant_identity: str,
    config_dict: Optional[dict[str, Any]] = None,
) -> Any:
    """Create the outbound SIP participant and wait for an answer/failure."""
    trunk_id = _outbound_trunk_id(config_dict)
    if not trunk_id:
        raise RuntimeError("Outbound SIP trunk not configured")

    async with api.LiveKitAPI() as lk:
        return await lk.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                room_name=room_name,
                sip_trunk_id=trunk_id,
                sip_call_to=phone_number,
                participant_identity=participant_identity,
                wait_until_answered=True,
            )
        )


async def _delete_room_quietly(room_name: str) -> None:
    """Best-effort cleanup for rooms left idle after API-owned dial failures."""
    try:
        async with api.LiveKitAPI() as lk:
            await lk.room.delete_room(api.DeleteRoomRequest(room=room_name))
    except Exception:
        pass


@app.post("/calls", response_model=CallResponse)
async def create_call(
    request: CallRequest,
    authorization: Optional[str] = Header(default=None),
) -> CallResponse:
    _require_auth(authorization)
    normalized_phone = normalize_phone_number(request.phone_number)
    call_id = f"call_{uuid.uuid4().hex[:12]}"
    room_name = f"agent_call_{call_id}"
    participant_identity = f"sip_{normalized_phone}"
    dispatch_metadata = {
        **request.metadata,
        "call_id": call_id,
        "call_direction": "outbound",
        "outbound_dial_mode": "api",
        "phone_number": normalized_phone,
        "sip_participant_identity": participant_identity,
        "call_purpose": request.purpose,
        "requested_by": request.requested_by,
        "agent_type": request.agent_type,
        "customer_name": request.customer_name,
        "company_name": request.company_name,
        "source": request.metadata.get("source", request.requested_by),
    }
    dispatch_metadata = {key: value for key, value in dispatch_metadata.items() if value is not None}

    create_call_record({
        "call_id": call_id,
        "room_name": room_name,
        "phone_number": normalized_phone,
        "status": "dispatching",
        "metadata": dispatch_metadata,
        "participant_identity": participant_identity,
        "created_at": now_iso(),
        "event_message": "Call dispatch requested",
    })

    try:
        await _create_room_and_dispatch_agent(room_name, dispatch_metadata)
    except Exception as err:
        update_call_record(
            call_id,
            status="dispatch_failed",
            reason="dispatch_error",
            error=str(err),
            event_message="LiveKit agent dispatch failed",
        )
        raise HTTPException(status_code=502, detail=f"Failed to dispatch LiveKit agent: {err}") from err

    update_call_record(call_id, status="dispatched", event_message="LiveKit agent dispatched")

    try:
        sip_info = await _create_outbound_sip_participant(
            room_name=room_name,
            phone_number=normalized_phone,
            participant_identity=participant_identity,
            config_dict=dispatch_metadata,
        )
    except api.TwirpError as err:
        metadata = getattr(err, "metadata", {}) or {}
        sip_status_code = metadata.get("sip_status_code")
        sip_status = metadata.get("sip_status")
        error_message = getattr(err, "message", None) or getattr(err, "msg", None) or str(err)
        reason = sip_failure_reason(sip_status_code, sip_status, error_message)
        status = failure_status_for_reason(reason)
        update_call_record(
            call_id,
            status=status,
            reason=reason,
            sip_status_code=sip_status_code,
            sip_status=sip_status,
            error=error_message,
            event_message="Outbound SIP call failed",
        )
        await _delete_room_quietly(room_name)
        return CallResponse(
            ok=False,
            call_id=call_id,
            room_name=room_name,
            status=status,
            phone_number=normalized_phone,
            reason=reason,
            sip_status_code=str(sip_status_code) if sip_status_code is not None else None,
            sip_status=sip_status,
            error=error_message,
        )
    except Exception as err:
        update_call_record(
            call_id,
            status="failed",
            reason="api_dial_error",
            error=str(err),
            event_message="Outbound SIP call failed",
        )
        await _delete_room_quietly(room_name)
        return CallResponse(
            ok=False,
            call_id=call_id,
            room_name=room_name,
            status="failed",
            phone_number=normalized_phone,
            reason="api_dial_error",
            error=str(err),
        )

    sip_call_id = getattr(sip_info, "sip_call_id", None)
    update_call_record(
        call_id,
        status="answered",
        sip_call_id=sip_call_id,
        participant_identity=participant_identity,
        event_message="Outbound SIP call answered",
    )
    return CallResponse(
        ok=True,
        call_id=call_id,
        room_name=room_name,
        status="answered",
        phone_number=normalized_phone,
        sip_call_id=sip_call_id,
    )


@app.get("/calls/{call_id}")
def get_call(
    call_id: str,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _require_auth(authorization)
    record = get_call_record(call_id)
    if not record:
        raise HTTPException(status_code=404, detail="Call not found")
    return record
