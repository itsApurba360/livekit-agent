import asyncio
import json
import logging
import os
import re
import uuid
from collections import Counter

logger = logging.getLogger("call-api")
from typing import Any, Optional
from urllib.parse import parse_qs

from call_outcomes import failure_status_for_reason, sip_failure_reason
from call_status_store import (
    create_call_record,
    get_call_record,
    get_call_record_by_vobiz_call_uuid,
    get_setting,
    list_active_call_records,
    list_call_records,
    now_iso,
    set_setting,
    update_call_record,
)
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Query, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from livekit import api
from pydantic import BaseModel, Field, field_validator
from vobiz_client import VobizRestClient, find_recording_for_call

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
load_dotenv()

AGENT_NAME = os.environ.get("LIVEKIT_AGENT_NAME") or os.environ.get("AGENT_NAME") or "outbound-caller"


app = FastAPI(title="LiveKit Call Control API", version="0.1.0")

DASHBOARD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard")
dashboard_templates = Jinja2Templates(directory=os.path.join(DASHBOARD_DIR, "templates"))
app.mount("/dashboard/static", StaticFiles(directory=os.path.join(DASHBOARD_DIR, "static")), name="dashboard-static")


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
    live_statuses = {"dispatching", "dispatched", "agent_ready", "dialing"}
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


def _internal_token() -> str:
    return os.environ.get("CALL_API_INTERNAL_TOKEN", "").strip() or _call_api_token()


def _require_internal_auth(authorization: Optional[str] = None, token: Optional[str] = None) -> None:
    expected_token = _internal_token()
    if not expected_token:
        raise HTTPException(status_code=503, detail="CALL_API_INTERNAL_TOKEN or CALL_API_TOKEN is not configured")
    supplied_token = (token or "").strip()
    if not supplied_token and authorization and authorization.startswith("Bearer "):
        supplied_token = authorization.removeprefix("Bearer ").strip()
    if supplied_token != expected_token:
        raise HTTPException(status_code=403, detail="Invalid internal token")


def _transcript_text_from_report(report: Any) -> Optional[str]:
    if not isinstance(report, dict):
        return None
    lines: list[str] = []
    items = report.get("items") or report.get("messages") or []
    if isinstance(items, dict):
        items = items.get("items") or items.get("messages") or []
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            role = item.get("role") or item.get("type") or "unknown"
            content = item.get("content") or item.get("text")
            if isinstance(content, list):
                parts = []
                for part in content:
                    if isinstance(part, str):
                        parts.append(part)
                    elif isinstance(part, dict):
                        parts.append(str(part.get("text") or part.get("content") or ""))
                content = " ".join(part for part in parts if part)
            if content:
                lines.append(f"{role}: {content}")
    return "\n".join(lines) if lines else None


def _store_vobiz_recording_result(call_id: str, result: Optional[dict[str, Any]]) -> bool:
    if not result:
        return False
    return update_call_record(
        call_id,
        vobiz_call_uuid=result.get("vobiz_call_uuid"),
        vobiz_recording_id=result.get("vobiz_recording_id"),
        recording_source=result.get("recording_source") or "vobiz",
        recording_url=result.get("recording_url"),
        recording_duration_ms=result.get("recording_duration_ms"),
        recording_format=result.get("recording_format"),
        recording_type=result.get("recording_type"),
        event_message="Vobiz recording metadata updated",
        event_details={key: value for key, value in result.items() if key != "raw_recording"},
    )


def _refresh_vobiz_recording_for_call(call_id: str) -> Optional[dict[str, Any]]:
    record = get_call_record(call_id)
    if not record:
        return None
    result = find_recording_for_call(record)
    _store_vobiz_recording_result(call_id, result)
    return result


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
    agent_type: Optional[str] = Field(default="support", description="Optional override: support or sales")
    customer_name: Optional[str] = None
    company_name: Optional[str] = None
    contact_person: Optional[str] = None
    gender: Optional[str] = None
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
def dashboard(request: Request) -> HTMLResponse:
    return dashboard_templates.TemplateResponse(request, "dashboard.html", {})


@app.get("/auth/verify")
def verify_auth(authorization: Optional[str] = Header(default=None)) -> dict[str, bool]:
    _require_auth(authorization)
    return {"ok": True}


class SpreadsheetSettingsRequest(BaseModel):
    spreadsheet_url: str = Field(..., description="Google Sheets URL or raw spreadsheet ID")


@app.get("/settings/spreadsheet")
def get_spreadsheet_settings(authorization: Optional[str] = Header(default=None)) -> dict[str, Any]:
    _require_auth(authorization)
    return {
        "ok": True,
        "spreadsheet_url": get_setting("spreadsheet_url"),
        "spreadsheet_id": get_setting("spreadsheet_id"),
        "validated_at": get_setting("spreadsheet_validated_at"),
        "warnings": get_setting("spreadsheet_warnings", []),
    }


@app.post("/settings/spreadsheet")
def save_spreadsheet_settings(
    request: SpreadsheetSettingsRequest,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _require_auth(authorization)
    from sheet_calling_automation import parse_spreadsheet_id, validate_spreadsheet

    spreadsheet_id = parse_spreadsheet_id(request.spreadsheet_url)
    if not spreadsheet_id:
        raise HTTPException(status_code=422, detail="Could not parse a spreadsheet ID from that link")

    result = validate_spreadsheet(spreadsheet_id)
    if not result["ok"]:
        raise HTTPException(status_code=422, detail="; ".join(result["errors"]))

    set_setting("spreadsheet_url", request.spreadsheet_url.strip())
    set_setting("spreadsheet_id", spreadsheet_id)
    set_setting("spreadsheet_validated_at", now_iso())
    set_setting("spreadsheet_warnings", result["warnings"])
    return {
        "ok": True,
        "spreadsheet_id": spreadsheet_id,
        "warnings": result["warnings"],
    }


class GoogleCredsRequest(BaseModel):
    creds_json: str = Field(..., description="Service account JSON content or Base64 string")


@app.get("/settings/google-creds")
def get_google_creds_settings(authorization: Optional[str] = Header(default=None)) -> dict[str, Any]:
    _require_auth(authorization)
    from call_status_store import get_setting

    creds_json = get_setting("google_sheets_creds_json") or os.environ.get("GOOGLE_SHEETS_CREDS_JSON")
    configured = False
    client_email = None

    if creds_json and creds_json.strip():
        try:
            from sheet_calling_automation import parse_google_creds_info
            info = parse_google_creds_info(creds_json)
            if info and "client_email" in info:
                configured = True
                client_email = info.get("client_email")
        except Exception:
            pass

    if not configured and os.path.exists(".google_sheets_creds.json"):
        try:
            with open(".google_sheets_creds.json") as f:
                d = json.load(f)
                client_email = d.get("client_email")
                configured = True
        except Exception:
            pass

    return {
        "ok": True,
        "configured": configured,
        "client_email": client_email,
        "updated_at": get_setting("google_creds_updated_at"),
    }


@app.post("/settings/google-creds")
def save_google_creds_settings(
    request: GoogleCredsRequest,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _require_auth(authorization)
    from sheet_calling_automation import parse_google_creds_info
    from google.oauth2.service_account import Credentials

    try:
        info = parse_google_creds_info(request.creds_json)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid credentials format: {e}")

    if not info or not isinstance(info, dict):
        raise HTTPException(status_code=422, detail="Invalid Google Service Account JSON structure.")

    if "private_key" not in info or "client_email" not in info:
        raise HTTPException(status_code=422, detail="JSON missing required 'private_key' or 'client_email' fields.")

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    try:
        Credentials.from_service_account_info(info, scopes=scopes)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Failed to create Google credentials: {e}")

    compact_json = json.dumps(info)
    set_setting("google_sheets_creds_json", compact_json)
    set_setting("google_creds_updated_at", now_iso())

    try:
        with open(".google_sheets_creds.json", "w") as f:
            f.write(compact_json)
    except Exception:
        pass

    if os.path.exists(AGENT_ERROR_LOG_PATH):
        try:
            os.remove(AGENT_ERROR_LOG_PATH)
        except Exception:
            pass

    return {
        "ok": True,
        "client_email": info.get("client_email"),
        "project_id": info.get("project_id"),
        "message": f"Successfully saved Google Service Account credentials for {info.get('client_email')}",
    }


class CallingWindowRequest(BaseModel):
    enabled: bool
    start: str = Field(..., description="HH:MM, IST")
    end: str = Field(..., description="HH:MM, IST")

    @field_validator("start", "end")
    @classmethod
    def validate_time_format(cls, value: str) -> str:
        from datetime import datetime as _dt

        try:
            _dt.strptime(value, "%H:%M")
        except ValueError:
            raise ValueError("Time must be in HH:MM format")
        return value


@app.get("/settings/calling-window")
def get_calling_window_settings(authorization: Optional[str] = Header(default=None)) -> dict[str, Any]:
    _require_auth(authorization)
    return {
        "ok": True,
        "enabled": get_setting("calling_window_enabled", False),
        "start": get_setting("calling_window_start", "10:00"),
        "end": get_setting("calling_window_end", "18:30"),
    }


@app.post("/settings/calling-window")
def save_calling_window_settings(
    request: CallingWindowRequest,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _require_auth(authorization)
    if request.start >= request.end:
        raise HTTPException(status_code=422, detail="Window start must be earlier than end")

    set_setting("calling_window_enabled", request.enabled)
    set_setting("calling_window_start", request.start)
    set_setting("calling_window_end", request.end)
    return {"ok": True, "enabled": request.enabled, "start": request.start, "end": request.end}


def _parse_byte_range(range_header: str, total: int) -> tuple[Optional[int], Optional[int]]:
    """Parse ``bytes=start-end``; returns inclusive (start, end) or (None, None) if invalid."""
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", (range_header or "").strip())
    if not match or total <= 0:
        return None, None
    start_s, end_s = match.groups()
    if start_s and end_s:
        start, end = int(start_s), int(end_s)
    elif start_s:
        start, end = int(start_s), total - 1
    elif end_s:
        suffix = int(end_s)
        start = max(total - suffix, 0)
        end = total - 1
    else:
        return None, None
    if start < 0 or end < start or start >= total:
        return None, None
    end = min(end, total - 1)
    return start, end


@app.get("/dashboard/data")
def dashboard_data(
    limit: int = Query(default=100, ge=1, le=500),
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _require_auth(authorization)
    calls = list_call_records(limit=limit)
    active_calls = [c for c in calls if c.get("status") in {"dispatching", "dispatched", "agent_ready", "dialing", "answered", "active"}]
    is_running = len(active_calls) > 0 or os.path.exists("agent_running.flag")
    
    agent_error = None
    if os.path.exists("agent_error.log"):
        try:
            with open("agent_error.log", "r") as f:
                agent_error = f.read().strip()
        except Exception:
            pass
            
    return {
        "ok": True,
        "summary": _dashboard_summary(calls),
        "calls": calls,
        "agent_running": is_running,
        "agent_error": agent_error,
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


def _sip_outbound_config() -> Optional[Any]:
    """Return an inline SIPOutboundConfig if hostname + auth are provided in env."""
    hostname = (
        os.environ.get("SIP_TRUNK_HOSTNAME")
        or os.environ.get("VOBIZ_SIP_DOMAIN")
        or os.environ.get("SIP_HOSTNAME")
        or os.environ.get("OUTBOUND_SIP_HOSTNAME")
        or ""
    ).strip().rstrip("/")

    username = (
        os.environ.get("SIP_AUTH_USERNAME")
        or os.environ.get("SIP_TRUNK_USERNAME")
        or os.environ.get("VOBIZ_SIP_USERNAME")
        or ""
    ).strip()

    password = (
        os.environ.get("SIP_AUTH_PASSWORD")
        or os.environ.get("SIP_TRUNK_PASSWORD")
        or os.environ.get("VOBIZ_SIP_PASSWORD")
        or ""
    ).strip()

    if not hostname or not username or not password:
        return None

    return api.SIPOutboundConfig(
        hostname=hostname,
        auth_username=username,
        auth_password=password,
    )


def _sip_from_number() -> Optional[str]:
    return (
        os.environ.get("SIP_NUMBER")
        or os.environ.get("SIP_FROM_NUMBER")
        or os.environ.get("DEFAULT_TRANSFER_NUMBER")
        or None
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
    trunk_cfg = _sip_outbound_config()
    from_number = _sip_from_number()

    async with api.LiveKitAPI() as lk:
        if trunk_cfg:
            # Prefer inline config (allows passing auth directly)
            req = api.CreateSIPParticipantRequest(
                room_name=room_name,
                trunk=trunk_cfg,
                sip_call_to=phone_number,
                participant_identity=participant_identity,
                wait_until_answered=True,
            )
            if from_number:
                req.sip_number = from_number
            return await lk.sip.create_sip_participant(req)
        else:
            if not trunk_id:
                raise RuntimeError("Outbound SIP trunk not configured")
            req = api.CreateSIPParticipantRequest(
                room_name=room_name,
                sip_trunk_id=trunk_id,
                sip_call_to=phone_number,
                participant_identity=participant_identity,
                wait_until_answered=True,
            )
            return await lk.sip.create_sip_participant(req)


async def _delete_room_quietly(room_name: str) -> None:
    """Best-effort cleanup for rooms left idle after API-owned dial failures."""
    try:
        async with api.LiveKitAPI() as lk:
            await lk.room.delete_room(api.DeleteRoomRequest(room=room_name))
    except Exception:
        pass


async def _wait_for_agent_to_join(room_name: str, timeout_seconds: float = 30.0) -> bool:
    # ponytail: wait for agent to prevent races, fallback to dial if timeout (increased from 10.0 to 30.0 for cold starts)
    start_time = asyncio.get_event_loop().time()
    logger.info(f"Waiting for agent to join room {room_name} (timeout: {timeout_seconds}s)")
    while asyncio.get_event_loop().time() - start_time < timeout_seconds:
        try:
            async with api.LiveKitAPI() as lk:
                res = await lk.room.list_participants(api.ListParticipantsRequest(room=room_name))
                non_sip_participants = [
                    p for p in res.participants
                    if not p.identity.startswith("sip_")
                ]
                if non_sip_participants:
                    elapsed = asyncio.get_event_loop().time() - start_time
                    logger.info(f"Agent worker {non_sip_participants[0].identity} detected in room {room_name} after {elapsed:.2f}s")
                    return True
        except Exception as err:
            logger.warning(f"Error checking participants in room {room_name}: {err}")
        await asyncio.sleep(0.5)
    logger.warning(f"Timed out waiting for agent to join room {room_name} after {timeout_seconds}s")
    return False


async def _wait_for_agent_ready(call_id: str, timeout_seconds: float = 60.0) -> bool:
    # ponytail: wait for agent realtime session to be ready (increased from 30.0 to 60.0)
    start_time = asyncio.get_event_loop().time()
    logger.info("Waiting for agent realtime session to be ready for call %s", call_id)
    while asyncio.get_event_loop().time() - start_time < timeout_seconds:
        record = get_call_record(call_id) or {}
        status = record.get("status")
        if status in {"agent_ready", "active", "answered", "completed"}:
            logger.info("Agent ready for call %s with status %s", call_id, status)
            return True
        if status == "dispatch_failed" or str(status or "").startswith("failed"):
            return False
        await asyncio.sleep(0.5)
    logger.warning("Timed out waiting for agent_ready for call %s after %.1fs", call_id, timeout_seconds)
    return False


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
        "contact_person": request.contact_person,
        "gender": request.gender,
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

    # Wait for the agent to join and finish realtime startup before dialing.
    await _wait_for_agent_to_join(room_name)
    if not await _wait_for_agent_ready(call_id):
        update_call_record(
            call_id,
            status="dispatch_failed",
            reason="agent_ready_timeout",
            error="Agent realtime session did not become ready before dialing; check worker/provider logs.",
            event_message="LiveKit agent did not become ready before dialing",
        )
        await _delete_room_quietly(room_name)
        return CallResponse(
            ok=False,
            call_id=call_id,
            room_name=room_name,
            status="dispatch_failed",
            phone_number=normalized_phone,
            reason="agent_ready_timeout",
            error="LiveKit agent did not become ready before dialing",
        )

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


@app.post("/calls/{call_id}/refresh-recording")
def refresh_call_recording(
    call_id: str,
    token: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    # Support both header and ?token= (for direct links)
    if token and not (authorization and str(authorization).startswith("Bearer ")):
        authorization = f"Bearer {token}"
    _require_auth(authorization)
    record = get_call_record(call_id)
    if not record:
        raise HTTPException(status_code=404, detail="Call not found")
    result = _refresh_vobiz_recording_for_call(call_id)
    return {"ok": bool(result and result.get("recording_url")), "recording": result, "call": get_call_record(call_id)}


def get_active_calls() -> list[dict[str, Any]]:
    try:
        return list_active_call_records()
    except Exception:
        return []


@app.get("/agent/status")
def agent_status(authorization: Optional[str] = Header(default=None)) -> dict[str, Any]:
    _require_auth(authorization)
    active_calls = get_active_calls()
    is_running = len(active_calls) > 0 or os.path.exists("agent_running.flag")
    
    agent_error = None
    if os.path.exists("agent_error.log"):
        try:
            with open("agent_error.log", "r") as f:
                agent_error = f.read().strip()
        except Exception:
            pass
            
    return {
        "ok": True,
        "running": is_running,
        "active_calls": active_calls,
        "agent_error": agent_error,
    }


async def _run_sheets_automation_wrapper(ignore_schedule: bool = False):
    if os.path.exists("agent_stop.flag"):
        try:
            os.remove("agent_stop.flag")
        except Exception:
            pass
    # Create running flag
    with open("agent_running.flag", "w") as f:
        f.write("running")
    try:
        from sheet_calling_automation import run_sheets_automation
        import logging
        logger = logging.getLogger("call-api")
        
        while not os.path.exists("agent_stop.flag"):
            try:
                await run_sheets_automation(ignore_schedule=ignore_schedule)
            except Exception as loop_err:
                logger.error(f"Error in sheets automation loop cycle: {loop_err}")
                
            # Sleep for 15 seconds, checking for stop flag every second
            for _ in range(15):
                if os.path.exists("agent_stop.flag"):
                    break
                await asyncio.sleep(1)
                
    except Exception as err:
        import logging
        logging.getLogger("call-api").error(f"Fatal error in sheets automation wrapper: {err}")
    finally:
        try:
            if os.path.exists("agent_running.flag"):
                os.remove("agent_running.flag")
        except Exception:
            pass


@app.post("/agent/start")
async def start_agent(
    background_tasks: BackgroundTasks,
    ignore_schedule: bool = Query(default=False),
    authorization: Optional[str] = Header(default=None)
) -> dict[str, Any]:
    _require_auth(authorization)
    if os.path.exists("agent_running.flag") or len(get_active_calls()) > 0:
        return {"ok": False, "message": "Agent is already running"}
    
    background_tasks.add_task(_run_sheets_automation_wrapper, ignore_schedule)
    return {"ok": True, "message": "Agent started successfully"}


@app.post("/agent/kill")
async def kill_agent(authorization: Optional[str] = Header(default=None)) -> dict[str, Any]:
    _require_auth(authorization)
    
    # Create stop flag for the loop
    with open("agent_stop.flag", "w") as f:
        f.write("stop")
    
    # Remove running flag
    try:
        if os.path.exists("agent_running.flag"):
            os.remove("agent_running.flag")
    except Exception:
        pass

    # Find and delete all active rooms in LiveKit to cut calls
    active_calls = get_active_calls()
    killed_count = 0
    for call in active_calls:
        room_name = call["room_name"]
        call_id = call["call_id"]
        try:
            async with api.LiveKitAPI() as lk:
                await lk.room.delete_room(api.DeleteRoomRequest(room=room_name))
            killed_count += 1
        except Exception:
            pass
        
        # Update PostgreSQL call record to failed/killed
        update_call_record(
            call_id,
            status="failed",
            reason="killed",
            event_message="Call manually terminated via global Kill Switch",
        )
        
    return {"ok": True, "message": f"Stop flag set and {killed_count} active calls cut."}


@app.post("/calls/{call_id}/kill")
async def kill_call(call_id: str, authorization: Optional[str] = Header(default=None)) -> dict[str, Any]:
    _require_auth(authorization)
    record = get_call_record(call_id)
    if not record:
        raise HTTPException(status_code=404, detail="Call not found")
    
    room_name = record["room_name"]
    try:
        async with api.LiveKitAPI() as lk:
            await lk.room.delete_room(api.DeleteRoomRequest(room=room_name))
    except Exception:
        pass
    
    update_call_record(
        call_id,
        status="failed",
        reason="killed",
        event_message="Call manually terminated via dashboard",
    )
    return {"ok": True, "message": "Call terminated successfully."}


@app.post("/internal/calls/{call_id}/session-report")
def store_session_report(
    call_id: str,
    payload: dict[str, Any],
    authorization: Optional[str] = Header(default=None),
    token: Optional[str] = Query(default=None),
) -> dict[str, Any]:
    _require_internal_auth(authorization, token)
    record = get_call_record(call_id)
    if not record:
        raise HTTPException(status_code=404, detail="Call not found")

    report = payload.get("report") if isinstance(payload.get("report"), dict) else payload.get("session_report")
    if not isinstance(report, dict):
        report = {"items": payload.get("items") or payload.get("messages") or []}
    transcript_text = payload.get("transcript_text") or payload.get("transcript") or _transcript_text_from_report(report)
    update_call_record(
        call_id,
        transcript_source=payload.get("transcript_source") or "livekit",
        transcript_text=transcript_text,
        session_report=report,
        event_message="LiveKit session report stored",
        event_details={"transcript_source": payload.get("transcript_source") or "livekit"},
    )

    recording_result = None
    try:
        recording_result = _refresh_vobiz_recording_for_call(call_id)
    except Exception as err:
        update_call_record(
            call_id,
            event_message="Vobiz recording lookup failed",
            event_details={"error": str(err)},
        )
    return {"ok": True, "recording": recording_result, "call": get_call_record(call_id)}


async def _payload_from_request(request: Request) -> dict[str, Any]:
    content_type = request.headers.get("content-type", "")
    raw_body = await request.body()
    if "application/json" in content_type:
        try:
            parsed = json.loads(raw_body.decode("utf-8") or "{}")
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    parsed_qs = parse_qs(raw_body.decode("utf-8"), keep_blank_values=True)
    return {key: values[-1] if values else "" for key, values in parsed_qs.items()}


@app.post("/internal/vobiz/recording-callback")
async def vobiz_recording_callback(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    token: Optional[str] = Query(default=None),
    call_id: Optional[str] = Query(default=None),
) -> dict[str, Any]:
    _require_internal_auth(authorization, token)
    payload = await _payload_from_request(request)
    vobiz_call_uuid = payload.get("call_uuid") or payload.get("CallUUID")
    recording_id = payload.get("recording_id") or payload.get("RecordingID")
    recording_url = payload.get("record_url") or payload.get("recording_url") or payload.get("RecordUrl")
    if not vobiz_call_uuid and not recording_id:
        raise HTTPException(status_code=422, detail="call_uuid or recording_id is required")

    record = get_call_record(call_id) if call_id else None
    if not record and vobiz_call_uuid:
        record = get_call_record_by_vobiz_call_uuid(str(vobiz_call_uuid))
    if not record:
        raise HTTPException(status_code=404, detail="No call record is linked to this Vobiz callback")

    update_call_record(
        record["call_id"],
        vobiz_call_uuid=str(vobiz_call_uuid) if vobiz_call_uuid else None,
        vobiz_recording_id=str(recording_id) if recording_id else None,
        recording_source="vobiz",
        recording_url=str(recording_url) if recording_url else None,
        recording_duration_ms=payload.get("recording_duration_ms") or payload.get("RecordingDurationMs"),
        event_message="Vobiz recording callback stored",
        event_details=payload,
    )
    return {"ok": True, "call_id": record["call_id"]}


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


@app.get("/calls/{call_id}/recording")
def get_call_recording(call_id: str, request: Request):
    record = get_call_record(call_id)
    if not record:
        raise HTTPException(status_code=404, detail="Call not found")

    rec_url = record.get("recording_url")
    if not rec_url:
        raise HTTPException(status_code=404, detail="No recording available for this call")

    range_header = request.headers.get("range")
    fmt = (record.get("recording_format") or "wav").lower()
    filename = f"{call_id}.{fmt}"
    base_headers = {
        "Content-Disposition": f'inline; filename="{filename}"',
        "Accept-Ranges": "bytes",
    }

    try:
        client = VobizRestClient()
        upstream = client.stream_recording_media(rec_url, range_header=range_header)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch recording from Vobiz: {e}")

    content_type = upstream.headers.get("Content-Type", "audio/wav")

    # If origin ignores Range, slice locally so <audio> can seek/scrub.
    if range_header and upstream.status_code == 200:
        try:
            data = upstream.content
        finally:
            upstream.close()
        total = len(data)
        start, end = _parse_byte_range(range_header, total)
        if start is None:
            raise HTTPException(status_code=416, detail="Invalid range")
        chunk = data[start : end + 1]
        headers = {
            **base_headers,
            "Content-Range": f"bytes {start}-{end}/{total}",
            "Content-Length": str(len(chunk)),
        }
        return StreamingResponse(iter([chunk]), status_code=206, media_type=content_type, headers=headers)

    out_headers = dict(base_headers)
    if upstream.headers.get("Content-Length"):
        out_headers["Content-Length"] = upstream.headers["Content-Length"]
    if upstream.headers.get("Content-Range"):
        out_headers["Content-Range"] = upstream.headers["Content-Range"]

    def iter_chunks():
        try:
            for chunk in upstream.iter_content(chunk_size=64 * 1024):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    return StreamingResponse(
        iter_chunks(),
        status_code=upstream.status_code,
        media_type=content_type,
        headers=out_headers,
    )
