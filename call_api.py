# -*- coding: utf-8 -*-
import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from livekit import api
from pydantic import BaseModel, Field, field_validator

load_dotenv()

AGENT_NAME = os.environ.get("LIVEKIT_AGENT_NAME") or os.environ.get("AGENT_NAME") or "outbound-caller"

app = FastAPI(title="LiveKit Call Control API", version="0.1.0")
CALL_RECORDS: dict[str, dict[str, Any]] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


async def _dispatch_livekit_agent(room_name: str, dispatch_metadata: dict[str, Any]) -> Any:
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


@app.post("/calls", response_model=CallResponse)
async def create_call(
    request: CallRequest,
    authorization: Optional[str] = Header(default=None),
) -> CallResponse:
    _require_auth(authorization)
    normalized_phone = normalize_phone_number(request.phone_number)
    call_id = f"call_{uuid.uuid4().hex[:12]}"
    room_name = f"agent_call_{call_id}"
    dispatch_metadata = {
        **request.metadata,
        "call_id": call_id,
        "call_direction": "outbound",
        "phone_number": normalized_phone,
        "call_purpose": request.purpose,
        "requested_by": request.requested_by,
        "agent_type": request.agent_type,
        "customer_name": request.customer_name,
        "company_name": request.company_name,
        "source": request.metadata.get("source", request.requested_by),
    }
    dispatch_metadata = {key: value for key, value in dispatch_metadata.items() if value is not None}

    CALL_RECORDS[call_id] = {
        "call_id": call_id,
        "room_name": room_name,
        "phone_number": normalized_phone,
        "status": "dispatching",
        "metadata": dispatch_metadata,
        "created_at": _now_iso(),
    }

    try:
        await _dispatch_livekit_agent(room_name, dispatch_metadata)
    except Exception as err:
        CALL_RECORDS[call_id]["status"] = "dispatch_failed"
        CALL_RECORDS[call_id]["error"] = str(err)
        raise HTTPException(status_code=502, detail=f"Failed to dispatch LiveKit agent: {err}") from err

    CALL_RECORDS[call_id]["status"] = "dispatched"
    return CallResponse(
        ok=True,
        call_id=call_id,
        room_name=room_name,
        status="dispatched",
        phone_number=normalized_phone,
    )


@app.get("/calls/{call_id}")
def get_call(
    call_id: str,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _require_auth(authorization)
    record = CALL_RECORDS.get(call_id)
    if not record:
        raise HTTPException(status_code=404, detail="Call not found")
    return record
