# -*- coding: utf-8 -*-
import os
import certifi

# Fix for macOS SSL Certificate errors
os.environ['SSL_CERT_FILE'] = certifi.where()

import logging
import json
import asyncio
from dotenv import load_dotenv
from livekit import agents, api
from livekit.agents import AgentSession, Agent
from livekit.agents.voice.room_io import RoomOptions, AudioInputOptions
from dataclasses import dataclass
from typing import Any, Optional

# Import local decoupled modules
from frappe_client import FrappeRestClient
from agent_tools import CustomerQueryTools

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
- CRITICAL: You are NOT allowed to say you sent a code without executing `send_verification_otp` first. Only after the tool returns success, say: "मैंने आपके व्हाट्सएप पर एक वेरिफिकेशन कोड भेजा है। कृपया मुझे वह कोड बताएं या अपने फोन कीपैड पर टाइप करें।"
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
        )

    if not phone_number:
        parts = room_name.split("_")
        if parts and parts[0].isdigit() and len(parts[0]) >= 10:
            phone_number = parts[0]
            direction = "inbound"
            source = "room_name"

    return CallContext(direction=direction, phone_number=phone_number, source=source)


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


async def _ensure_outbound_participant(
    ctx: agents.JobContext,
    call_context: CallContext,
    config_dict: dict[str, Any],
) -> CallContext:
    if not call_context.is_outbound:
        return call_context

    if not call_context.phone_number:
        logger.error("Outbound call requested without phone_number metadata.")
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
        ctx.shutdown(reason="Outbound SIP trunk not configured")
        call_context.ready = False
        return call_context

    logger.info("Initiating outbound SIP call to %s using trunk %s.", call_context.phone_number, trunk_id)
    try:
        await ctx.api.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                room_name=ctx.room.name,
                sip_trunk_id=trunk_id,
                sip_call_to=call_context.phone_number,
                participant_identity=participant_identity,
                wait_until_answered=True,
            )
        )
    except api.TwirpError as err:
        logger.error(
            "Failed to create outbound SIP participant: %s SIP status: %s %s",
            getattr(err, "message", str(err)),
            getattr(err, "metadata", {}).get("sip_status_code"),
            getattr(err, "metadata", {}).get("sip_status"),
        )
        ctx.shutdown(reason="Outbound SIP call failed")
        call_context.ready = False
        return call_context
    except Exception as err:
        logger.error("Failed to create outbound SIP participant: %s", err)
        ctx.shutdown(reason="Outbound SIP call failed")
        call_context.ready = False
        return call_context

    participant = await _wait_for_sip_participant(ctx, participant_identity=participant_identity, use_job_wait=True)
    return _build_call_context(ctx, config_dict, sip_participant=participant)


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

    if call_context.is_outbound:
        lines.append(
            "- This is an outbound call placed by LSA Office. The customer or lead did not call us in this session. "
            "Do not speak before the callee answers or before they speak first. On your first response after they speak, "
            "briefly introduce yourself, LSA Office, and the reason for calling."
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
    from livekit.plugins import openai, google, noise_cancellation

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
    lead_name = caller_info.get("name", "जी")
    company_name = caller_info.get("company", "हमारी कंपनी")

    agent_type = "Support" if caller_status == "Customer" else "Sales"
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

        prompt_base += f"\n\n{_call_context_prompt(call_context)}"

        return prompt_base

    system_prompt = get_compiled_prompt(is_verified=False)

    try:
        initial_greeting = initial_greeting_template.format(lead_name=lead_name, company_name=company_name)
    except Exception:
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
        ctx=ctx
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
        session = AgentSession(llm=realtime_llm)
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
            session = AgentSession(llm=realtime_llm, tts=custom_tts)
        else:
            realtime_llm = openai.realtime.RealtimeModel(
                model=model,
                voice=voice.lower() if voice else "alloy",
                api_key=ai_api_key,
            )
            session = AgentSession(llm=realtime_llm)
    else:
        raise ValueError(f"Unsupported provider: {provider}")
    fnc_ctx.session = session

    agent_tools = list(fnc_ctx.function_tools.values())

    # For outbound PSTN, LiveKit recommends dialing and waiting for answer before
    # starting the AgentSession so the callee does not hear a partial greeting.
    call_context = await _ensure_outbound_participant(ctx, call_context, config_dict)
    if not call_context.ready:
        return
    phone_number = call_context.phone_number
    fnc_ctx.phone_number = phone_number
    system_prompt = get_compiled_prompt(is_verified=fnc_ctx.is_verified)

    # Start LiveKit Agent Session
    nc_option = noise_cancellation.BVCTelephony() if agent_config.get("noise_cancellation", False) else None
    agent_instance = StandaloneAgent(instructions=system_prompt, tools=agent_tools)
    await session.start(
        room=ctx.room,
        agent=agent_instance,
        room_options=RoomOptions(
            audio_input=AudioInputOptions(
                noise_cancellation=nc_option,
            ),
            close_on_disconnect=True,
            delete_room_on_close=True,
        ),
    )

    # Greet user at startup
    if "3.1" not in model and not call_context.is_outbound:
        await asyncio.sleep(0.75)  # Wait for connection clicks/pops to settle
        await session.generate_reply(
            instructions=f"[System Note: Introduce yourself to the customer with this greeting: '{initial_greeting}']"
        )
    elif call_context.is_outbound:
        logger.info("Outbound call: waiting for callee to speak before generating a reply.")


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
        )
    )
