# -*- coding: utf-8 -*-
import asyncio
import logging
import os
import json
import requests
from dataclasses import dataclass
from typing import Any, Optional

from livekit import agents, api
from livekit.agents import AgentSession

from call_outcomes import failure_status_for_reason, sip_failure_reason
from call_status_store import get_call_record, update_call_record


logger = logging.getLogger("remote-agent")
agent_config: dict[str, Any] = {}


def set_agent_config(config: dict[str, Any]) -> None:
    global agent_config
    agent_config = config


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


def _participant_matches_call(call_context: CallContext, participant: Any) -> bool:
    identity = str(getattr(participant, "identity", "") or "")
    if call_context.participant_identity and identity == call_context.participant_identity:
        return True
    participant_phone = _participant_phone_number(participant)
    return bool(participant_phone and participant_phone == call_context.phone_number)


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
