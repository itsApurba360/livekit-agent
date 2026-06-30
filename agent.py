# -*- coding: utf-8 -*-
import os
import certifi

# Fix for macOS SSL Certificate errors
os.environ['SSL_CERT_FILE'] = certifi.where()

import logging
import json
import asyncio
import requests
from dotenv import load_dotenv
from livekit import agents, api
from livekit.agents import AgentSession, Agent
from livekit.agents.voice.room_io import RoomOptions, AudioInputOptions
from dataclasses import dataclass
from typing import Any, Optional

# Import local decoupled modules
from frappe_client import FrappeRestClient
from agent_tools import CustomerQueryTools
from call_outcomes import failure_status_for_reason, sip_failure_reason
from call_status_store import get_call_record, update_call_record

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("remote-agent")

# Load Local Configuration File
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "agent_config.json")
try:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        agent_config = json.load(f)
    logger.info("Successfully loaded agent configurations from agent_config.json.")
except Exception as config_err:
    logger.error(f"Failed to load agent_config.json: {config_err}. Using hardcoded fallbacks.")
    agent_config = {}

# Default Configurations fallback
DEFAULT_TRANSFER_NUMBER = os.environ.get("DEFAULT_TRANSFER_NUMBER")
SIP_DOMAIN = os.environ.get("VOBIZ_SIP_DOMAIN")
AGENT_NAME = os.environ.get("LIVEKIT_AGENT_NAME") or os.environ.get("AGENT_NAME") or "outbound-caller"

DEFAULT_SUPPORT_UNVERIFIED_RULES = """- The current call is linked to a registered customer number. You may answer questions about sales orders, invoice status, outstanding amounts, and customer info directly using your tools (get_customer_sales_orders, get_customer_pending_amount, get_customer_details, get_sales_order_details, get_sales_invoice_details) without any WhatsApp verification.
- Do NOT ask for verification at the start of the call or for voice-only queries.
- WhatsApp verification is ONLY required when the customer asks to receive information via WhatsApp (text details such as order ID, balance, customer name, or a PDF copy of a Sales Order or Sales Invoice).
- When the customer asks for WhatsApp delivery, you MUST call `send_verification_otp` immediately. Do not ask for permission first.
- When they speak the code, call `verify_otp` to check if it matches. Only after successful verification, call `send_text_whatsapp` for text details or `send_pdf_whatsapp` for PDF documents.
- If the customer is not registered on WhatsApp (indicated by the `send_verification_otp` tool), ask them to provide a valid WhatsApp number."""

DEFAULT_SUPPORT_VERIFIED_RULES = """- The customer is verified for WhatsApp delivery. You may call `send_text_whatsapp` to send requested details as a text message, or `send_pdf_whatsapp` to send a Sales Order or Sales Invoice PDF.
- Do NOT repeat or mention verification, OTP, or verification status in subsequent turns. Continue helping with their request.
- You may still use all customer query tools freely for voice answers."""


@dataclass
class CallContext:
    direction: str = "web"
    phone_number: Optional[str] = None
    participant_identity: Optional[str] = None
    sip_call_status: Optional[str] = None
    sip_call_id: Optional[str] = None
    sip_rule_id: Optional[str] = None
    sip_trunk_id: Optional[str] = None
    source: str = "metadata"
    ready: bool = True
    call_id: Optional[str] = None
    call_purpose: Optional[str] = None
    requested_by: Optional[str] = None
    last_conversation_history: Optional[str] = None

    @property
    def is_outbound(self) -> bool:
        return self.direction == "outbound"

    @property
    def is_inbound(self) -> bool:
        return self.direction == "inbound"


def _load_json_dict(raw_value: Any) -> dict[str, Any]:
    if not raw_value:
        return {}
    if isinstance(raw_value, dict):
        return raw_value
    try:
        parsed = json.loads(raw_value)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _normalize_direction(value: Any) -> Optional[str]:
    if not value:
        return None
    normalized = str(value).strip().lower().replace("-", "_")
    if normalized in {"outbound", "outgoing", "dial_out", "dialout", "agent_outbound"}:
        return "outbound"
    if normalized in {"inbound", "incoming", "pstn_inbound", "sip_inbound"}:
        return "inbound"
    if normalized in {"web", "browser", "mobile", "app"}:
        return "web"
    return None


def _participant_attrs(participant: Any) -> dict[str, Any]:
    attrs = getattr(participant, "attributes", None)
    return attrs if isinstance(attrs, dict) else {}


def _is_sip_participant(participant: Any) -> bool:
    attrs = _participant_attrs(participant)
    identity = str(getattr(participant, "identity", "") or "")
    kind = str(getattr(participant, "kind", "") or "").lower()
    return (
        identity.startswith("sip_")
        or "sip" in kind
        or any(key.startswith("sip.") for key in attrs)
    )


def _participant_phone_number(participant: Any) -> Optional[str]:
    attrs = _participant_attrs(participant)
    phone_number = attrs.get("sip.phoneNumber")
    if phone_number:
        return str(phone_number)

    identity = str(getattr(participant, "identity", "") or "")
    if identity.startswith("sip_"):
        return identity.replace("sip_", "", 1)

    return None


def _remote_participants(ctx: agents.JobContext) -> list[Any]:
    participants = getattr(getattr(ctx, "room", None), "remote_participants", {}) or {}
    return list(participants.values())


def _find_sip_participant(ctx: agents.JobContext, participant_identity: Optional[str] = None) -> Optional[Any]:
    for participant in _remote_participants(ctx):
        identity = getattr(participant, "identity", None)
        if participant_identity and identity == participant_identity:
            return participant
        if not participant_identity and _is_sip_participant(participant):
            return participant
    return None


def _direction_from_metadata(config_dict: dict[str, Any], room_name: str) -> str:
    for key in ("call_direction", "direction", "call_type"):
        direction = _normalize_direction(config_dict.get(key))
        if direction:
            return direction

    if room_name.startswith("agent_call_"):
        return "outbound"

    return "web"


def _agent_type_from_metadata(value: Any) -> Optional[str]:
    if not value:
        return None
    normalized = str(value).strip().lower()
    if normalized in {"support", "customer", "kavya"}:
        return "Support"
    if normalized in {"sales", "lead", "nandini"}:
        return "Sales"
    return None


def _select_agent_type(config_dict: dict[str, Any], caller_status: Any) -> str:
    metadata_agent_type = _agent_type_from_metadata(config_dict.get("agent_type"))
    if metadata_agent_type:
        return metadata_agent_type
        
    # Check default_agent from global config
    default_agent = agent_config.get("default_agent")
    if default_agent:
        normalized = default_agent.strip().lower()
        if "support" in normalized:
            return "Support"
        if "sales" in normalized:
            return "Sales"

    # If this is an outbound call, default to Support
    if config_dict.get("call_direction") == "outbound":
        return "Support"

    return "Support" if caller_status == "Customer" else "Sales"


def _build_call_context(
    ctx: agents.JobContext,
    config_dict: dict[str, Any],
    sip_participant: Optional[Any] = None,
) -> CallContext:
    room_name = getattr(ctx.room, "name", "") or ""
    direction = _direction_from_metadata(config_dict, room_name)
    phone_number = config_dict.get("phone_number") or config_dict.get("caller_phone")
    source = "metadata" if phone_number else "unknown"
    call_id = config_dict.get("call_id")
    call_purpose = config_dict.get("call_purpose") or config_dict.get("purpose")
    requested_by = config_dict.get("requested_by") or config_dict.get("source")

    participant = sip_participant or _find_sip_participant(ctx)
    if participant:
        attrs = _participant_attrs(participant)
        participant_phone = _participant_phone_number(participant)
        if participant_phone:
            phone_number = participant_phone
            source = "sip_attributes" if attrs.get("sip.phoneNumber") else "sip_identity"
        if attrs.get("sip.ruleID") and direction != "outbound":
            direction = "inbound"
        elif _is_sip_participant(participant) and direction == "web":
            direction = "inbound"

        return CallContext(
            direction=direction,
            phone_number=phone_number,
            participant_identity=getattr(participant, "identity", None),
            sip_call_status=attrs.get("sip.callStatus"),
            sip_call_id=attrs.get("sip.callIDFull") or attrs.get("sip.callID"),
            sip_rule_id=attrs.get("sip.ruleID"),
            sip_trunk_id=attrs.get("sip.trunkID"),
            source=source,
            call_id=call_id,
            call_purpose=call_purpose,
            requested_by=requested_by,
            last_conversation_history=config_dict.get("last_conversation_history") or config_dict.get("history"),
        )

    if not phone_number:
        parts = room_name.split("_")
        if parts and parts[0].isdigit() and len(parts[0]) >= 10:
            phone_number = parts[0]
            direction = "inbound"
            source = "room_name"

    return CallContext(
        direction=direction,
        phone_number=phone_number,
        source=source,
        call_id=call_id,
        call_purpose=call_purpose,
        requested_by=requested_by,
        last_conversation_history=config_dict.get("last_conversation_history") or config_dict.get("history"),
    )


async def _wait_for_sip_participant(
    ctx: agents.JobContext,
    participant_identity: Optional[str] = None,
    attempts: int = 30,
    delay_seconds: float = 0.1,
    use_job_wait: bool = False,
) -> Optional[Any]:
    if use_job_wait and hasattr(ctx, "wait_for_participant"):
        try:
            if participant_identity:
                participant = await ctx.wait_for_participant(identity=participant_identity)
            else:
                participant = await ctx.wait_for_participant()
            if participant and _is_sip_participant(participant):
                return participant
        except Exception as err:
            logger.warning("ctx.wait_for_participant did not return a SIP participant: %s", err)

    for attempt in range(attempts):
        participant = _find_sip_participant(ctx, participant_identity=participant_identity)
        if participant:
            logger.info("Detected SIP participant on attempt %s: %s", attempt + 1, getattr(participant, "identity", None))
            return participant
        await asyncio.sleep(delay_seconds)
    return None


def _outbound_trunk_id(config_dict: dict[str, Any]) -> Optional[str]:
    return (
        config_dict.get("outbound_trunk_id")
        or config_dict.get("sip_trunk_id")
        or agent_config.get("outbound_trunk_id")
        or os.environ.get("OUTBOUND_TRUNK_ID")
        or os.environ.get("LIVEKIT_OUTBOUND_TRUNK_ID")
        or os.environ.get("SIP_OUTBOUND_TRUNK_ID")
    )


def _dialed_by_api(config_dict: dict[str, Any]) -> bool:
    return str(config_dict.get("outbound_dial_mode") or "").strip().lower() == "api"


def _safe_update_call_record(call_context: CallContext, **updates: Any) -> None:
    """Persist call status when a call_id is available, without breaking the worker."""
    if not call_context.call_id:
        return
    try:
        updated = update_call_record(call_context.call_id, **updates)
        if not updated:
            logger.debug("Call record %s was not found for status update", call_context.call_id)
    except Exception as err:
        logger.warning("Failed to update call record %s: %s", call_context.call_id, err)


def _call_record_has_failure_status(call_context: CallContext) -> bool:
    if not call_context.call_id:
        return False
    try:
        record = get_call_record(call_context.call_id)
    except Exception as err:
        logger.warning("Failed to read call record %s before status update: %s", call_context.call_id, err)
        return False
    status = str((record or {}).get("status") or "")
    return status.startswith("failed") or status == "dispatch_failed"


def _call_api_internal_url() -> Optional[str]:
    raw = (
        os.environ.get("CALL_API_INTERNAL_URL")
        or os.environ.get("LIVEKIT_CALL_API_URL")
        or os.environ.get("CALL_API_URL")
        or ""
    ).strip()
    return raw.rstrip("/") if raw else None


def _call_api_internal_token() -> Optional[str]:
    return (
        os.environ.get("CALL_API_INTERNAL_TOKEN")
        or os.environ.get("CALL_API_TOKEN")
        or os.environ.get("LIVEKIT_CALL_API_TOKEN")
        or ""
    ).strip() or None


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


def _session_report_payload(session: AgentSession, ctx: agents.JobContext, call_context: CallContext) -> dict[str, Any]:
    try:
        report = session.history.to_dict()
    except Exception as err:
        logger.warning("Failed to serialize LiveKit session history: %s", err)
        report = {}
    return {
        "room_name": getattr(ctx.room, "name", None),
        "transcript_source": "livekit",
        "transcript_text": _transcript_text_from_report(report),
        "report": report,
        "recording_source": "vobiz",
        "sip_call_id": call_context.sip_call_id,
        "participant_identity": call_context.participant_identity,
    }


def _post_session_report_sync(call_id: str, payload: dict[str, Any]) -> None:
    base_url = _call_api_internal_url()
    token = _call_api_internal_token()
    if not base_url or not token:
        logger.info("Skipping session report callback; CALL_API_INTERNAL_URL/LIVEKIT_CALL_API_URL or token is not configured")
        return
    response = requests.post(
        f"{base_url}/internal/calls/{call_id}/session-report",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=20,
    )
    response.raise_for_status()


async def _post_session_report(call_id: str, payload: dict[str, Any]) -> None:
    try:
        await asyncio.to_thread(_post_session_report_sync, call_id, payload)
        logger.info("Posted LiveKit session report for call %s", call_id)
    except Exception as err:
        logger.warning("Failed to post LiveKit session report for call %s: %s", call_id, err)


def _register_session_report_handler(ctx: agents.JobContext, call_context: CallContext, session: AgentSession) -> None:
    if not call_context.is_outbound or not call_context.call_id or not hasattr(ctx.room, "on"):
        return
    posted = False

    @ctx.room.on("participant_disconnected")
    def on_participant_disconnected_for_report(participant):
        nonlocal posted
        if posted or not _participant_matches_call(call_context, participant):
            return
        posted = True

        async def send_later() -> None:
            await asyncio.sleep(2)
            payload = _session_report_payload(session, ctx, call_context)
            await _post_session_report(call_context.call_id or "", payload)

        asyncio.create_task(send_later())


def _sip_failure_reason(
    sip_status_code: Any = None,
    sip_status: Any = None,
    message: Any = None,
) -> str:
    return sip_failure_reason(sip_status_code, sip_status, message)


def _failure_status_for_reason(reason: str) -> str:
    return failure_status_for_reason(reason)


def _participant_matches_call(call_context: CallContext, participant: Any) -> bool:
    identity = str(getattr(participant, "identity", "") or "")
    if call_context.participant_identity and identity == call_context.participant_identity:
        return True
    participant_phone = _participant_phone_number(participant)
    return bool(participant_phone and participant_phone == call_context.phone_number)


def _register_call_status_handlers(ctx: agents.JobContext, call_context: CallContext, session: AgentSession) -> None:
    """Track post-answer SIP state transitions in the PostgreSQL call record."""
    if not call_context.is_outbound or not call_context.call_id or not hasattr(ctx.room, "on"):
        return

    @ctx.room.on("participant_attributes_changed")
    def on_participant_attributes_changed(changed_attributes, participant):
        if not _participant_matches_call(call_context, participant):
            return
        attrs = _participant_attrs(participant)
        sip_call_status = attrs.get("sip.callStatus") or (changed_attributes or {}).get("sip.callStatus")
        if not sip_call_status:
            return
        normalized = str(sip_call_status).lower()
        if normalized == "active":
            status = "active"
        elif normalized == "disconnected":
            status = "completed"
        else:
            status = None
        if status:
            _safe_update_call_record(
                call_context,
                status=status,
                participant_status=str(sip_call_status),
                sip_call_id=attrs.get("sip.callIDFull") or attrs.get("sip.callID"),
                participant_identity=getattr(participant, "identity", None),
                event_message=f"SIP participant status changed to {sip_call_status}",
            )

    @ctx.room.on("participant_disconnected")
    def on_participant_disconnected(participant):
        if not _participant_matches_call(call_context, participant):
            return
        if _call_record_has_failure_status(call_context):
            logger.info("Preserving failure status for call %s after participant disconnect", call_context.call_id)
            return
        attrs = _participant_attrs(participant)
        disconnect_reason = getattr(participant, "disconnect_reason", None)
        _safe_update_call_record(
            call_context,
            status="completed",
            reason=str(disconnect_reason) if disconnect_reason else "participant_disconnected",
            participant_status=attrs.get("sip.callStatus") or "disconnected",
            sip_call_id=attrs.get("sip.callIDFull") or attrs.get("sip.callID"),
            participant_identity=getattr(participant, "identity", None),
            event_message="SIP participant disconnected",
        )

        # Also ensure LiveKit session report (transcript) is posted for outbound calls.
        # The dedicated report listener can miss quick disconnects; this path is reliable.
        if call_context.is_outbound and call_context.call_id:
            try:
                payload = _session_report_payload(session, ctx, call_context)
                asyncio.create_task(_post_session_report(call_context.call_id, payload))
            except Exception as err:
                logger.warning("Failed to schedule session report on disconnect for %s: %s", call_context.call_id, err)


async def _ensure_outbound_participant(
    ctx: agents.JobContext,
    call_context: CallContext,
    config_dict: dict[str, Any],
) -> CallContext:
    if not call_context.is_outbound:
        return call_context

    if not call_context.phone_number:
        logger.error("Outbound call requested without phone_number metadata.")
        _safe_update_call_record(
            call_context,
            status="failed",
            reason="missing_phone_number",
            error="Outbound call requested without phone_number metadata",
            event_message="Outbound call failed before dialing",
        )
        ctx.shutdown(reason="Outbound call missing phone number")
        call_context.ready = False
        return call_context

    participant_identity = call_context.participant_identity or f"sip_{call_context.phone_number}"
    existing_participant = _find_sip_participant(ctx, participant_identity=participant_identity) or _find_sip_participant(ctx)
    if existing_participant:
        logger.info("Outbound SIP participant already exists: %s", getattr(existing_participant, "identity", None))
        return _build_call_context(ctx, config_dict, sip_participant=existing_participant)

    trunk_id = _outbound_trunk_id(config_dict)
    if not trunk_id:
        logger.error("Outbound call requested, but no outbound SIP trunk id is configured.")
        _safe_update_call_record(
            call_context,
            status="failed_trunk",
            reason="trunk_not_configured",
            error="Outbound SIP trunk not configured",
            event_message="Outbound call failed before dialing",
        )
        ctx.shutdown(reason="Outbound SIP trunk not configured")
        call_context.ready = False
        return call_context

    logger.info("Initiating outbound SIP call to %s using trunk %s.", call_context.phone_number, trunk_id)
    _safe_update_call_record(
        call_context,
        status="dialing",
        event_message="Outbound SIP dialing started",
        event_details={"trunk_id": trunk_id, "phone_number": call_context.phone_number},
    )
    try:
        sip_participant_info = await ctx.api.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                room_name=ctx.room.name,
                sip_trunk_id=trunk_id,
                sip_call_to=call_context.phone_number,
                participant_identity=participant_identity,
                wait_until_answered=True,
            )
        )
    except api.TwirpError as err:
        metadata = getattr(err, "metadata", {}) or {}
        sip_status_code = metadata.get("sip_status_code")
        sip_status = metadata.get("sip_status")
        error_message = getattr(err, "message", str(err))
        reason = _sip_failure_reason(sip_status_code, sip_status, error_message)
        logger.error(
            "Failed to create outbound SIP participant: %s SIP status: %s %s",
            error_message,
            sip_status_code,
            sip_status,
        )
        _safe_update_call_record(
            call_context,
            status=_failure_status_for_reason(reason),
            reason=reason,
            sip_status_code=sip_status_code,
            sip_status=sip_status,
            error=error_message,
            event_message="Outbound SIP call failed",
        )
        ctx.shutdown(reason="Outbound SIP call failed")
        call_context.ready = False
        return call_context
    except Exception as err:
        logger.error("Failed to create outbound SIP participant: %s", err)
        _safe_update_call_record(
            call_context,
            status="failed",
            reason="worker_error",
            error=str(err),
            event_message="Outbound SIP call failed",
        )
        ctx.shutdown(reason="Outbound SIP call failed")
        call_context.ready = False
        return call_context

    _safe_update_call_record(
        call_context,
        status="answered",
        sip_call_id=getattr(sip_participant_info, "sip_call_id", None),
        event_message="Outbound SIP call answered",
    )

    participant = await _wait_for_sip_participant(ctx, participant_identity=participant_identity, use_job_wait=True)
    updated_context = _build_call_context(ctx, config_dict, sip_participant=participant)
    if participant:
        _safe_update_call_record(
            updated_context,
            status="active",
            participant_identity=getattr(participant, "identity", None),
            participant_status=updated_context.sip_call_status,
            sip_call_id=updated_context.sip_call_id,
            event_message="SIP participant active in room",
        )
    return updated_context


async def _wait_for_api_dialed_outbound_participant(
    ctx: agents.JobContext,
    call_context: CallContext,
    config_dict: dict[str, Any],
) -> CallContext:
    if not call_context.is_outbound:
        return call_context

    participant_identity = (
        config_dict.get("sip_participant_identity")
        or call_context.participant_identity
        or (f"sip_{call_context.phone_number}" if call_context.phone_number else None)
    )
    participant = await _wait_for_sip_participant(
        ctx,
        participant_identity=participant_identity,
        use_job_wait=True,
    )
    for _attempt in range(90):
        if participant:
            sip_status = str(_participant_attrs(participant).get("sip.callStatus") or "").strip().lower()
            if sip_status == "active":
                break
            if sip_status == "disconnected":
                participant = None
                break
        await asyncio.sleep(1)
        participant = _find_sip_participant(ctx, participant_identity=participant_identity)

    if not participant or str(_participant_attrs(participant).get("sip.callStatus") or "").strip().lower() != "active":
        logger.error("API-dialed outbound call did not get SIP participant: %s", participant_identity)
        _safe_update_call_record(
            call_context,
            status="failed",
            reason="missing_sip_participant",
            error="API-dialed outbound call did not get SIP participant",
            event_message="Outbound SIP participant did not join",
        )
        ctx.shutdown(reason="Outbound SIP participant did not join")
        call_context.ready = False
        return call_context

    updated_context = _build_call_context(ctx, config_dict, sip_participant=participant)
    _safe_update_call_record(
        updated_context,
        status="active",
        participant_identity=getattr(participant, "identity", None),
        participant_status=updated_context.sip_call_status,
        sip_call_id=updated_context.sip_call_id,
        event_message="API-dialed SIP participant active in room",
    )
    return updated_context


async def _prepare_outbound_participant(
    ctx: agents.JobContext,
    call_context: CallContext,
    config_dict: dict[str, Any],
) -> CallContext:
    if call_context.is_outbound and _dialed_by_api(config_dict):
        return await _wait_for_api_dialed_outbound_participant(ctx, call_context, config_dict)
    return await _ensure_outbound_participant(ctx, call_context, config_dict)


def _call_context_prompt(call_context: CallContext) -> str:
    lines = [
        "Call context:",
        f"- Direction: {call_context.direction}.",
    ]
    if call_context.phone_number:
        if call_context.is_outbound:
            lines.append(f"- Callee phone number: {call_context.phone_number}.")
        elif call_context.is_inbound:
            lines.append(f"- Caller phone number: {call_context.phone_number}.")
        else:
            lines.append(f"- Participant phone number: {call_context.phone_number}.")
    if call_context.sip_call_status:
        lines.append(f"- Current SIP call status: {call_context.sip_call_status}.")
    if call_context.sip_call_id:
        lines.append(f"- SIP call id: {call_context.sip_call_id}.")
    if call_context.sip_rule_id:
        lines.append(f"- Inbound SIP dispatch rule id: {call_context.sip_rule_id}.")
    if call_context.sip_trunk_id:
        lines.append(f"- SIP trunk id: {call_context.sip_trunk_id}.")
    if call_context.call_id:
        lines.append(f"- Call id: {call_context.call_id}.")
    if call_context.call_purpose:
        lines.append(f"- Call purpose: {call_context.call_purpose}.")
    if call_context.requested_by:
        lines.append(f"- Requested by: {call_context.requested_by}.")
    if call_context.last_conversation_history:
        lines.append(f"- Last conversation history: {call_context.last_conversation_history}.")

    if call_context.is_outbound:
        lines.append(
            "- This is an outbound call placed by LSA Office. The customer or lead did not call us in this session. "
            "Do not speak before the callee answers or before they speak first. On your first response after they speak, "
            "introduce yourself as Kavya from LSA Office, and explain the reason/purpose of the call in simple, polite, spoken Hindi/Hinglish. "
            "Use the call purpose above as the reason when it is present; do not invent a different reason. "
            "Do NOT state the call purpose verbatim; instead, interpret and simplify it so it sounds natural and conversational to the customer. "
            "If they remain silent, you will be prompted to speak first."
        )
    elif call_context.is_inbound:
        lines.append(
            "- This is an inbound call. The user called LSA Office, so greet them as the caller, welcome them, "
            "and ask how you can help. Do not say that you called them, that you are following up on an enquiry, "
            "or that you wanted two minutes to understand their requirement unless the user or metadata says so."
        )
    else:
        lines.append(
            "- This is a web or app session. Treat it like a normal user-initiated support conversation. "
            "Do not imply LSA Office placed a phone call."
        )

    return "\n".join(lines)


def _select_initial_greeting_template(
    agent_settings: dict[str, Any],
    call_context: CallContext,
    fallback_greeting: str,
) -> str:
    if call_context.is_outbound:
        return (
            agent_settings.get("outbound_initial_greeting")
            or agent_settings.get("initial_greeting")
            or fallback_greeting
        )

    if call_context.is_inbound:
        return (
            agent_settings.get("inbound_initial_greeting")
            or agent_settings.get("initial_greeting")
            or fallback_greeting
        )

    return (
        agent_settings.get("web_initial_greeting")
        or agent_settings.get("inbound_initial_greeting")
        or agent_settings.get("initial_greeting")
        or fallback_greeting
    )


class StandaloneAgent(Agent):
    """
    Decoupled agent implementation supporting dynamically compiled instructions.
    """
    def __init__(self, instructions: str, tools: list) -> None:
        super().__init__(
            instructions=instructions,
            tools=tools,
            turn_detection=None,
        )

async def entrypoint(ctx: agents.JobContext):
    """
    Main entrypoint for the REST-decoupled agent worker.
    Handles both inbound (PSTN/web) and outbound (dial-out) calls.
    """
    from livekit.plugins import openai, google, ai_coustics

    logger.info(f"Connecting to room: {ctx.room.name}")

    # Initialize remote Frappe REST client
    frappe_url = os.environ.get("FRAPPE_SITE_URL")
    api_key = os.environ.get("FRAPPE_API_KEY")
    api_secret = os.environ.get("FRAPPE_API_SECRET")
    client = FrappeRestClient(base_url=frappe_url, api_key=api_key, api_secret=api_secret)

    # 1. Parse metadata. Room metadata can override dispatch metadata.
    job_metadata = _load_json_dict(getattr(ctx.job, "metadata", None))
    room_metadata = _load_json_dict(getattr(ctx.room, "metadata", None))
    config_dict = {**job_metadata, **room_metadata}

    # 2. Resolve call context before lookup so inbound SIP attributes can supply caller ID.
    call_context = _build_call_context(ctx, config_dict)
    if not call_context.phone_number and not call_context.is_outbound:
        logger.info("Waiting briefly for inbound SIP participant attributes.")
        sip_participant = await _wait_for_sip_participant(ctx)
        call_context = _build_call_context(ctx, config_dict, sip_participant=sip_participant)

    phone_number = call_context.phone_number
    logger.info(
        "Resolved call context: direction=%s phone=%s participant=%s source=%s status=%s",
        call_context.direction,
        call_context.phone_number,
        call_context.participant_identity,
        call_context.source,
        call_context.sip_call_status,
    )

    # 4. Perform Lookup of Caller via REST Client
    caller_info = {"status": "Unknown", "name": "जी", "company": "हमारी कंपनी"}
    if phone_number:
        try:
            logger.info(f"Performing remote lookup for caller phone: {phone_number}")
            caller_info = await asyncio.to_thread(client.lookup_caller, phone_number)
            logger.info(f"Remote caller lookup result: {caller_info}")
        except Exception as err:
            logger.error(f"Failed to lookup caller via REST API: {err}")

    # Determine which agent to launch
    # If customer matched -> launch Support Agent (Kavya)
    # If lead or unknown -> launch Sales Agent (Nandini)
    caller_status = caller_info.get("status")
    customer_id = caller_info.get("customer_id")
    lead_id = caller_info.get("lead_id")
    lead_name = config_dict.get("customer_name") or config_dict.get("name") or caller_info.get("name") or "जी"
    company_name = config_dict.get("company_name") or config_dict.get("company") or caller_info.get("company") or "हमारी कंपनी"
    customer_id = config_dict.get("customer_id") or config_dict.get("cid") or caller_info.get("customer_id")

    agent_type = _select_agent_type(config_dict, caller_status)
    logger.info(f"Launching agent type: {agent_type} (Caller status: {caller_status})")

    # Fetch configuration templates
    if agent_type == "Support":
        agent_settings = agent_config.get("support_agent", {})
        fallback_prompt = "You are Kavya, support assistant. Help customer with their queries."
        fallback_greeting = "नमस्ते, LSA Office में आपका स्वागत है। मैं काव्या हूँ। मैं आपकी क्या मदद कर सकती हूँ?"
    else:
        agent_settings = agent_config.get("sales_agent", {})
        fallback_prompt = "You are Nandini, sales assistant. Qualify the lead named {lead_name}."
        fallback_greeting = "नमस्ते {lead_name} जी, मैं नंदिनी बोल रही हूँ एलएसए ऑफिस से।"

    system_prompt_template = agent_settings.get("system_prompt", fallback_prompt)
    initial_greeting_template = _select_initial_greeting_template(
        agent_settings,
        call_context,
        fallback_greeting,
    )

    # Resolve dynamic system prompt compilation
    def get_compiled_prompt(is_verified: bool = False) -> str:
        nonlocal system_prompt_template, lead_name, company_name, customer_id, call_context
        format_dict = {
            "lead_name": lead_name,
            "company_name": company_name,
            "verification_rules": DEFAULT_SUPPORT_VERIFIED_RULES if is_verified else DEFAULT_SUPPORT_UNVERIFIED_RULES
        }
        try:
            prompt_base = system_prompt_template.format(**{k: v for k, v in format_dict.items() if f"{{{k}}}" in system_prompt_template})
        except Exception:
            prompt_base = system_prompt_template

        if "{verification_rules}" not in prompt_base and agent_type == "Support":
            rules_addon = DEFAULT_SUPPORT_VERIFIED_RULES if is_verified else DEFAULT_SUPPORT_UNVERIFIED_RULES
            prompt_base += f"\n\nSecurity Rules:\n{rules_addon}"

        if customer_id:
            prompt_base += f"\n\nThe current call is linked to Customer ID: '{customer_id}'. You can use tools (get_customer_sales_orders, get_sales_order_details, get_customer_pending_amount, get_sales_invoice_details, get_customer_details, send_text_whatsapp, send_pdf_whatsapp) to query their details. When explaining details, summarize in natural, polite Hindi/Hinglish. Do not read raw codes verbatim."
        else:
            prompt_base += "\n\nNo Customer is currently linked to this call. You can use the search_customer tool to find a customer by name or phone number."

        # Check if call purpose is a GST / document collection campaign
        purpose = (call_context.call_purpose or "").lower()
        is_gst_campaign = any(word in purpose for word in ["gst", "document", "filing", "pdf"])
        if is_gst_campaign:
            from datetime import datetime
            current_date_str = datetime.now().strftime("%d/%m/%Y")
            prompt_base += (
                f"\n\n--- CAMPAIGN RULES: GST DOCUMENT COLLECTION & FOLLOW-UP ---\n"
                f"- This call is for coordinating a follow-up call with a human executive regarding GST document collection. "
                f"Do NOT attempt to collect document details directly, and do NOT ask for OTP or WhatsApp verification.\n"
                f"- Inform the customer that LSA Office requires their GST documents for the filing period.\n"
                f"- Ask when a human executive from our team can call them back to collect the documents. "
                f"Once they provide a preferred date and time, call the `schedule_human_callback` tool with the details.\n"
                f"- IMPORTANT: If the customer mentions any problems, blockers, or conflicts (e.g. they cannot find their documents, need help, have travel/other conflicts, or prefer a human), you MUST immediately call the `schedule_human_callback` tool to assign a human callback to them with notes explaining the blockers.\n"
                f"- The current date is {current_date_str}. Convert relative dates like 'tomorrow' or 'today evening' to actual dates (DD/MM/YYYY) if possible before calling the tool.\n"
                f"- After scheduling, thank them politely in simple Hindi/Hinglish, then end the call using the `end_call` tool."
            )

        prompt_base += f"\n\n{_call_context_prompt(call_context)}"

        return prompt_base

    system_prompt = get_compiled_prompt(is_verified=False)

    try:
        initial_greeting = initial_greeting_template.format(
            lead_name=lead_name,
            name=lead_name,
            company_name=company_name,
            company=company_name,
            purpose=call_context.call_purpose or ""
        )
    except Exception as err:
        logger.warning(f"Failed to format initial greeting: {err}")
        initial_greeting = initial_greeting_template

    # Setup provider configurations
    provider = agent_config.get("provider", "Google")
    model = agent_config.get("model", "gemini-2.5-flash")
    voice = agent_config.get("voice", "Puck")

    # API Keys Resolution
    api_key_env = "GOOGLE_API_KEY" if provider == "Google" else "OPENAI_API_KEY"
    ai_api_key = os.environ.get(api_key_env)
    
    if not ai_api_key:
        logger.warning(f"No API key found in {api_key_env}. Agent initialization might fail.")

    is_gemini = (provider == "Google")

    # Setup Callback on verification success
    async def on_verification_success():
        nonlocal agent_instance, session, is_gemini
        logger.info("OTP Verification success callback triggered!")
        
        if not fnc_ctx.is_verified:
            fnc_ctx.is_verified = True
        fnc_ctx.generated_otp = None

        chat_ctx = agent_instance.chat_ctx.copy()
        
        # In-place prompt compilation swap
        new_system_prompt = get_compiled_prompt(is_verified=True)
        for msg in chat_ctx.messages():
            if msg.role == "system":
                msg.content = new_system_prompt
                break

        # Append one-time confirmation instruction
        chat_ctx.add_message(
            role="system",
            content="[System Note: WhatsApp verification successful. Confirm this once to the user by saying: 'धन्यवाद, आपका वेरिफिकेशन सफल रहा।' and then fulfill their WhatsApp request via send_text_whatsapp or send_pdf_whatsapp. Do not mention verification or OTP again.]"
        )
        await agent_instance.update_chat_ctx(chat_ctx)


        
        # Google Gemini Live / Realtime handles context updates natively. Others need manual prompt regeneration.
        if not is_gemini:
            session.generate_reply()

    # Initialize function context (passing REST client)
    fnc_ctx = CustomerQueryTools(
        client=client,
        customer_id=customer_id,
        phone_number=phone_number,
        on_verify_success=on_verification_success,
        ctx=ctx,
        call_id=call_context.call_id
    )

    # Initialize Realtime AI models
    logger.info(f"Initializing Standalone Agent Session ({provider} - Model: {model}, Voice: {voice})")
    if provider == "Google":
        realtime_llm = google.realtime.RealtimeModel(
            model=model,
            voice=voice,
            api_key=ai_api_key,
            instructions=system_prompt,
        )
        session = AgentSession(llm=realtime_llm, user_away_timeout=10.0)
    elif provider == "OpenAI":
        custom_tts_enabled = agent_config.get("custom_tts", False)
        custom_tts_voice = agent_config.get("custom_tts_voice", "Aoede")

        if custom_tts_enabled:
            realtime_llm = openai.realtime.RealtimeModel(
                model=model,
                modalities=["text"],
                api_key=ai_api_key,
            )
            google_api_key = os.environ.get("GOOGLE_API_KEY")
            custom_tts = google.beta.GeminiTTS(
                model="gemini-3.1-flash-tts-preview",
                api_key=google_api_key,
                voice_name=custom_tts_voice,
            )
            session = AgentSession(llm=realtime_llm, tts=custom_tts, user_away_timeout=10.0)
        else:
            realtime_llm = openai.realtime.RealtimeModel(
                model=model,
                voice=voice.lower() if voice else "alloy",
                api_key=ai_api_key,
            )
            session = AgentSession(llm=realtime_llm, user_away_timeout=10.0)
    else:
        raise ValueError(f"Unsupported provider: {provider}")
    fnc_ctx.session = session

    agent_tools = list(fnc_ctx.function_tools.values())

    # For outbound PSTN, LiveKit recommends dialing and waiting for answer before
    # starting the AgentSession so the callee does not hear a partial greeting.
    call_context = await _prepare_outbound_participant(ctx, call_context, config_dict)
    if not call_context.ready:
        return
    phone_number = call_context.phone_number
    fnc_ctx.phone_number = phone_number
    system_prompt = get_compiled_prompt(is_verified=fnc_ctx.is_verified)
    _register_call_status_handlers(ctx, call_context, session)
    _register_session_report_handler(ctx, call_context, session)

    # Robust inactivity tracking (10 seconds silence timeout)
    last_activity_time = asyncio.get_event_loop().time()
    is_user_speaking = False
    is_agent_speaking = False
    has_started_speaking = False
    silence_trigger_task = None

    def reset_activity():
        nonlocal last_activity_time
        last_activity_time = asyncio.get_event_loop().time()

    @session.on("user_state_changed")
    def on_user_state_changed(ev):
        nonlocal is_user_speaking, has_started_speaking
        reset_activity()
        if ev.new_state == "speaking":
            is_user_speaking = True
            has_started_speaking = True
            if silence_trigger_task and not silence_trigger_task.done():
                silence_trigger_task.cancel()
        else:
            is_user_speaking = False
        
        if ev.new_state == "away":
            logger.info("Inactivity timeout (away): ending call.")
            session.shutdown()

    @session.on("agent_state_changed")
    def on_agent_state_changed(ev):
        nonlocal is_agent_speaking, has_started_speaking
        reset_activity()
        if ev.new_state == "speaking":
            is_agent_speaking = True
            has_started_speaking = True
            if silence_trigger_task and not silence_trigger_task.done():
                silence_trigger_task.cancel()
        else:
            is_agent_speaking = False

    @session.on("user_input_transcribed")
    def on_user_input(ev):
        nonlocal has_started_speaking
        has_started_speaking = True
        if silence_trigger_task and not silence_trigger_task.done():
            silence_trigger_task.cancel()
        reset_activity()

    @session.on("conversation_item_added")
    def on_item_added(ev):
        nonlocal has_started_speaking
        has_started_speaking = True
        if silence_trigger_task and not silence_trigger_task.done():
            silence_trigger_task.cancel()
        reset_activity()

    async def inactivity_monitor():
        try:
            # Grace period for connection and initial greetings
            await asyncio.sleep(10)
            while True:
                await asyncio.sleep(1)
                if is_user_speaking or is_agent_speaking:
                    reset_activity()
                    continue
                elapsed = asyncio.get_event_loop().time() - last_activity_time
                if elapsed > 10.0:
                    logger.info(f"Silence timeout: no activity for {elapsed:.1f}s. Ending call.")
                    session.shutdown()
                    break
        except asyncio.CancelledError:
            pass

    monitor_task = asyncio.create_task(inactivity_monitor())

    @session.on("close")
    def on_close(ev):
        if monitor_task:
            monitor_task.cancel()
        if call_context.call_id:
            try:
                record = get_call_record(call_context.call_id)
                current_status = (record or {}).get("status")
                if current_status in ["active", "dispatched", "dispatching", "answered"]:
                    logger.info("Session closed: updating call status to completed")
                    _safe_update_call_record(
                        call_context,
                        status="completed",
                        reason="session_closed",
                        event_message="Agent session closed",
                    )
            except Exception as err:
                logger.warning("Failed to perform final call status cleanup on session close: %s", err)

    # Start LiveKit Agent Session
    nc_option = ai_coustics.audio_enhancement(model=ai_coustics.EnhancerModel.QUAIL_VF_S) if agent_config.get("noise_cancellation", False) else None
    agent_instance = StandaloneAgent(instructions=system_prompt, tools=agent_tools)
    room_options_kwargs = {
        "audio_input": AudioInputOptions(
            noise_cancellation=nc_option,
        ),
        "close_on_disconnect": True,
        "delete_room_on_close": True,
    }
    if call_context.participant_identity:
        room_options_kwargs["participant_identity"] = call_context.participant_identity
    await session.start(
        room=ctx.room,
        agent=agent_instance,
        room_options=RoomOptions(**room_options_kwargs),
    )

    # Greet user at startup
    if "3.1" not in model and not call_context.is_outbound:
        await asyncio.sleep(0.75)  # Wait for connection clicks/pops to settle
        await session.generate_reply(
            instructions=f"[System Note: Introduce yourself to the customer with this greeting: '{initial_greeting}']"
        )
    elif call_context.is_outbound:
        logger.info("Outbound call: waiting for callee to speak first.")
        # Start outbound silence monitor to speak first if caller is silent for 5 seconds
        async def outbound_silence_trigger():
            try:
                await asyncio.sleep(5.0)
                if not has_started_speaking and not is_user_speaking:
                    logger.info("Outbound initial silence detected (5 seconds). Agent speaking first.")
                    # Inject a system note to prompt the LLM to greet the user dynamically
                    await session.generate_reply(
                        instructions=(
                            f"[System Note: The customer has answered but remained silent. "
                            f"Initiate the call by greeting them dynamically and explaining the purpose: "
                            f"'{call_context.call_purpose or ''}'. Keep it natural, polite, and simple in Hindi.]"
                        )
                    )
            except asyncio.CancelledError:
                pass
        silence_trigger_task = asyncio.create_task(outbound_silence_trigger())


    # DTMF (keypad) Listener for OTP verification
    @ctx.room.on("sip_dtmf_received")
    def on_dtmf_received(dtmf):
        nonlocal fnc_ctx, session, agent_instance, is_gemini
        digit = dtmf.digit
        logger.info(f"DTMF digit received: {digit}")

        if fnc_ctx.generated_otp and not fnc_ctx.is_verified:
            if digit in "0123456789":
                fnc_ctx.dtmf_buffer += digit
                logger.info(f"DTMF buffer status: {fnc_ctx.dtmf_buffer}")

                if len(fnc_ctx.dtmf_buffer) == 4:
                    entered_otp = fnc_ctx.dtmf_buffer
                    fnc_ctx.dtmf_buffer = ""
                    if entered_otp == fnc_ctx.generated_otp:
                        logger.info("OTP verified successfully via keypad (DTMF).")
                        import asyncio
                        asyncio.create_task(on_verification_success())
                    else:
                        logger.info(f"Incorrect OTP entered via keypad: {entered_otp}")
                        async def process_incorrect_otp():
                            chat_ctx = agent_instance.chat_ctx.copy()
                            chat_ctx.add_message(
                                role="system",
                                content=f"[System Note: User entered incorrect OTP {entered_otp} via keypad. Ask them to retry or check their WhatsApp.]"
                            )
                            await agent_instance.update_chat_ctx(chat_ctx)
                            if not is_gemini:
                                session.generate_reply()
                        import asyncio
                        asyncio.create_task(process_incorrect_otp())

if __name__ == "__main__":
    agents.cli.run_app(
        agents.WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name=AGENT_NAME,
            load_threshold=0.99,
            initialize_process_timeout=30.0,
            port=int(os.environ.get("LIVEKIT_AGENT_HTTP_PORT", "8081")),
        )
    )
