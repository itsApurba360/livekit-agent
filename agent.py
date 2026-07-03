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
from typing import Any, Optional

# Import local decoupled modules
import agent_call_context as call_context_helpers
from agent_tools import CustomerQueryTools
from call_status_store import get_call_record, update_call_record

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("remote-agent")

# Load Local Configuration File
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "agent_config.json")
with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    agent_config = json.load(f)

# Validate required configuration keys
if "support_agent" not in agent_config:
    raise KeyError("Missing required configuration block: 'support_agent' in agent_config.json")

for key in ["system_prompt", "initial_greeting", "inbound_initial_greeting", "outbound_initial_greeting"]:
    if key not in agent_config["support_agent"]:
        raise KeyError(f"Missing required configuration key: 'support_agent.{key}' in agent_config.json")

logger.info("Successfully loaded agent configurations from agent_config.json.")
call_context_helpers.set_agent_config(agent_config)

# Default Configurations fallback
DEFAULT_TRANSFER_NUMBER = os.environ.get("DEFAULT_TRANSFER_NUMBER")
SIP_DOMAIN = os.environ.get("VOBIZ_SIP_DOMAIN")
AGENT_NAME = os.environ.get("LIVEKIT_AGENT_NAME") or os.environ.get("AGENT_NAME") or "outbound-caller"

DEFAULT_SUPPORT_UNVERIFIED_RULES = """- Customer lookup, WhatsApp, PDF, and OTP verification tools are disabled for now.
- Do not ask for OTP or WhatsApp verification.
- Use only outbound campaign scheduling tools and `end_call`."""

DEFAULT_SUPPORT_VERIFIED_RULES = DEFAULT_SUPPORT_UNVERIFIED_RULES


CallContext = call_context_helpers.CallContext


def _sync_call_context_dependencies() -> None:
    call_context_helpers.set_agent_config(agent_config)
    call_context_helpers.get_call_record = get_call_record
    call_context_helpers.update_call_record = update_call_record


def _load_json_dict(raw_value: Any) -> dict[str, Any]:
    return call_context_helpers._load_json_dict(raw_value)


def _normalize_direction(value: Any) -> Optional[str]:
    return call_context_helpers._normalize_direction(value)


def _participant_attrs(participant: Any) -> dict[str, Any]:
    return call_context_helpers._participant_attrs(participant)


def _is_sip_participant(participant: Any) -> bool:
    return call_context_helpers._is_sip_participant(participant)


def _participant_phone_number(participant: Any) -> Optional[str]:
    return call_context_helpers._participant_phone_number(participant)


def _remote_participants(ctx: agents.JobContext) -> list[Any]:
    return call_context_helpers._remote_participants(ctx)


def _find_sip_participant(ctx: agents.JobContext, participant_identity: Optional[str] = None) -> Optional[Any]:
    return call_context_helpers._find_sip_participant(ctx, participant_identity=participant_identity)


def _direction_from_metadata(config_dict: dict[str, Any], room_name: str) -> str:
    return call_context_helpers._direction_from_metadata(config_dict, room_name)


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
    # Always return Support agent as requested by configuration
    return "Support"


def _build_call_context(
    ctx: agents.JobContext,
    config_dict: dict[str, Any],
    sip_participant: Optional[Any] = None,
) -> CallContext:
    return call_context_helpers._build_call_context(ctx, config_dict, sip_participant=sip_participant)


async def _wait_for_sip_participant(
    ctx: agents.JobContext,
    participant_identity: Optional[str] = None,
    attempts: int = 30,
    delay_seconds: float = 0.1,
    use_job_wait: bool = False,
) -> Optional[Any]:
    return await call_context_helpers._wait_for_sip_participant(
        ctx,
        participant_identity=participant_identity,
        attempts=attempts,
        delay_seconds=delay_seconds,
        use_job_wait=use_job_wait,
    )


def _outbound_trunk_id(config_dict: dict[str, Any]) -> Optional[str]:
    _sync_call_context_dependencies()
    return call_context_helpers._outbound_trunk_id(config_dict)


def _dialed_by_api(config_dict: dict[str, Any]) -> bool:
    return call_context_helpers._dialed_by_api(config_dict)


def _safe_update_call_record(call_context: CallContext, **updates: Any) -> None:
    _sync_call_context_dependencies()
    return call_context_helpers._safe_update_call_record(call_context, **updates)


def _call_record_has_failure_status(call_context: CallContext) -> bool:
    _sync_call_context_dependencies()
    return call_context_helpers._call_record_has_failure_status(call_context)


def _call_api_internal_url() -> Optional[str]:
    return call_context_helpers._call_api_internal_url()


def _call_api_internal_token() -> Optional[str]:
    return call_context_helpers._call_api_internal_token()


def _transcript_text_from_report(report: Any) -> Optional[str]:
    return call_context_helpers._transcript_text_from_report(report)


def _session_report_payload(session: AgentSession, ctx: agents.JobContext, call_context: CallContext) -> dict[str, Any]:
    return call_context_helpers._session_report_payload(session, ctx, call_context)


def _post_session_report_sync(call_id: str, payload: dict[str, Any]) -> None:
    return call_context_helpers._post_session_report_sync(call_id, payload)


async def _post_session_report(call_id: str, payload: dict[str, Any]) -> None:
    return await call_context_helpers._post_session_report(call_id, payload)


def _register_session_report_handler(ctx: agents.JobContext, call_context: CallContext, session: AgentSession) -> None:
    _sync_call_context_dependencies()
    return call_context_helpers._register_session_report_handler(ctx, call_context, session)


def _sip_failure_reason(
    sip_status_code: Any = None,
    sip_status: Any = None,
    message: Any = None,
) -> str:
    return call_context_helpers._sip_failure_reason(sip_status_code, sip_status, message)


def _failure_status_for_reason(reason: str) -> str:
    return call_context_helpers._failure_status_for_reason(reason)


def _participant_matches_call(call_context: CallContext, participant: Any) -> bool:
    return call_context_helpers._participant_matches_call(call_context, participant)


def _register_call_status_handlers(ctx: agents.JobContext, call_context: CallContext, session: AgentSession) -> None:
    _sync_call_context_dependencies()
    return call_context_helpers._register_call_status_handlers(ctx, call_context, session)


async def _ensure_outbound_participant(
    ctx: agents.JobContext,
    call_context: CallContext,
    config_dict: dict[str, Any],
) -> CallContext:
    _sync_call_context_dependencies()
    return await call_context_helpers._ensure_outbound_participant(ctx, call_context, config_dict)


async def _wait_for_api_dialed_outbound_participant(
    ctx: agents.JobContext,
    call_context: CallContext,
    config_dict: dict[str, Any],
) -> CallContext:
    _sync_call_context_dependencies()
    return await call_context_helpers._wait_for_api_dialed_outbound_participant(ctx, call_context, config_dict)


async def _prepare_outbound_participant(
    ctx: agents.JobContext,
    call_context: CallContext,
    config_dict: dict[str, Any],
) -> CallContext:
    _sync_call_context_dependencies()
    return await call_context_helpers._prepare_outbound_participant(ctx, call_context, config_dict)


def _call_context_prompt(call_context: CallContext) -> str:
    return call_context_helpers._call_context_prompt(call_context)


def _select_initial_greeting_template(
    agent_settings: dict[str, Any],
    call_context: CallContext,
    fallback_greeting: str,
) -> str:
    return call_context_helpers._select_initial_greeting_template(agent_settings, call_context, fallback_greeting)


class StandaloneAgent(Agent):
    """
    Decoupled agent implementation supporting dynamically compiled instructions.
    """
    def __init__(self, instructions: str, tools: list) -> None:
        super().__init__(
            instructions=instructions,
            tools=tools,
        )

async def entrypoint(ctx: agents.JobContext):
    """
    Main entrypoint for the REST-decoupled agent worker.
    Handles both inbound (PSTN/web) and outbound (dial-out) calls.
    """
    from livekit.plugins import openai, google

    logger.info(f"Connecting to room: {ctx.room.name}")

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

    caller_info = {"status": "Unknown", "name": "जी", "company": "हमारी कंपनी"}

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
    agent_settings = agent_config["support_agent"]
    system_prompt_template = agent_settings["system_prompt"]
    initial_greeting_template = _select_initial_greeting_template(
        agent_settings,
        call_context,
        "",
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
            prompt_base += f"\n\nThe current outbound campaign row is linked to Customer ID: '{customer_id}'. Customer lookup and WhatsApp/PDF tools are disabled for now; use only scheduling or end-call tools."
        else:
            prompt_base += "\n\nNo Customer is currently linked to this outbound call. Customer lookup tools are disabled for now; continue with the call purpose and use only scheduling or end-call tools."

        prompt_base += (
            "\n\nLanguage rules:\n"
            "- Start in simple Hinglish (everyday spoken Hindi mixed with common English words) or natural, easy spoken language. Avoid formal, robotic, or ancient vocabulary in all languages.\n"
            "- Use modern English words for everyday terms rather than their formal translations (e.g., use 'papers' or 'documents' instead of 'दस्तावेज', 'callback' instead of 'पुनः कॉल', 'ready' instead of 'तैयार', 'time' instead of 'समय').\n"
            "- If the customer speaks another language, immediately switch to that language and keep using it until they ask to switch again.\n"
            "- Do not schedule an AI follow-up or human callback because of language preference or language mismatch. You can continue the same call in the customer's language."
        )

        from datetime import datetime, timedelta, timezone
        now_ist = datetime.now(timezone(timedelta(hours=5, minutes=30)))
        prompt_base += (
            "\n\nScheduling rules:\n"
            f"- Current IST date/time is {now_ist.strftime('%A, %d/%m/%Y %H:%M')}.\n"
            "- Schedule a later call only when the customer explicitly asks for it or clearly agrees to it.\n"
            "- Resolve natural date phrases like today, tomorrow, next Monday, next week, morning, afternoon, and evening into an exact DD/MM/YYYY date and HH:MM IST time before using a scheduling tool.\n"
            "- For requests like 'call me after 5 minutes' or '10 minutes later', call the scheduling tool with date_str='today' and time_str='+5 minutes' or '+10 minutes'. The tool resolves that from current IST; never use UTC or server-local time for these requests.\n"
            "- If the date or time is still unclear, ask one short clarification question instead of scheduling.\n"
            "- Before making the final entry, repeat the exact date and time to the customer and ask for confirmation.\n"
            "- First call the scheduling tool with confirmed=false to check office hours and availability, then call it with confirmed=true only after the customer clearly agrees.\n"
            "- Do not schedule outside 10:00 to 18:00 IST or on the LSA holiday list. If the requested slot is unavailable, offer the available slot returned by the tool and wait for confirmation.\n"
            "- Do not schedule for convenience, language mismatch, or uncertainty unless the customer gives or accepts a specific date and time."
        )

        is_sheet_campaign = call_context.requested_by == "sheets_automation"
        if is_sheet_campaign:
            current_date_str = now_ist.strftime("%d/%m/%Y")
            prompt_base += (
                f"\n\n--- CAMPAIGN RULES: DOCUMENT COLLECTION & FOLLOW-UP ---\n"
                f"- Ask the customer if their documents are ready.\n"
                f"- If they are ready, ask when they will send them. Once they provide a date or date and time, thank them politely and hang up using the `end_call` tool. Do NOT schedule any follow-up or human callback in this case.\n"
                f"- If they are not ready, or if they mention any blocker or need help, ask them what specific help/query they have. Collect their query, ask when they would be free for a callback from a human expert, schedule a human callback using `schedule_human_callback` with the callback time and the query in notes, and then hang up using `end_call`.\n"
                f"- Never ask the customer if they want an AI call or human call. Never mention these options.\n"
                f"- The current date is {current_date_str}. Convert relative dates like 'tomorrow' to actual dates (DD/MM/YYYY) for scheduling.\n"
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

    fnc_ctx = CustomerQueryTools(
        client=None,
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
    system_prompt = get_compiled_prompt(is_verified=fnc_ctx.is_verified)

    # Prepare the realtime session, but do not attach it to room audio until an
    # outbound PSTN participant has answered. This keeps the worker ready for
    # the API without listening to ringback/callertune early media.
    nc_option = None
    if agent_config.get("noise_cancellation", False):
        try:
            from livekit.plugins import ai_coustics

            nc_option = ai_coustics.audio_enhancement(model=ai_coustics.EnhancerModel.QUAIL_VF_S)
        except ImportError as err:
            logger.warning("Noise cancellation disabled; ai_coustics plugin unavailable: %s", err)
    
    agent_instance = StandaloneAgent(instructions=system_prompt, tools=agent_tools)
    if call_context.call_id:
        _safe_update_call_record(
            call_context,
            status="agent_ready",
            event_message="Agent worker ready for outbound dial",
        )

    # For outbound PSTN, wait for the participant to join (callee answers the phone)
    call_context = await _prepare_outbound_participant(ctx, call_context, config_dict)
    if not call_context.ready:
        return
    phone_number = call_context.phone_number
    fnc_ctx.phone_number = phone_number

    room_options_kwargs = {
        "audio_input": AudioInputOptions(
            noise_cancellation=nc_option,
        ),
        "close_on_disconnect": True,
        "delete_room_on_close": True,
    }
    if call_context.participant_identity:
        room_options_kwargs["participant_identity"] = call_context.participant_identity

    logger.info("Starting AgentSession after outbound participant is ready.")
    await session.start(
        room=ctx.room,
        agent=agent_instance,
        room_options=RoomOptions(**room_options_kwargs),
    )
    _register_call_status_handlers(ctx, call_context, session)
    _register_session_report_handler(ctx, call_context, session)

    # Robust inactivity tracking (10 seconds silence timeout)
    last_activity_time = asyncio.get_event_loop().time()
    is_user_speaking = False
    is_agent_speaking = False
    has_started_speaking = False
    silence_trigger_task = None
    # ponytail: gate the timer so it never counts ringing time on outbound calls
    first_activity_event = asyncio.Event()

    def reset_activity():
        nonlocal last_activity_time
        last_activity_time = asyncio.get_event_loop().time()
        # ponytail: do NOT set first_activity_event here — away/state-change events
        # would falsely signal activity. Only real speaking/transcript events do that.

    @session.on("user_state_changed")
    def on_user_state_changed(ev):
        nonlocal is_user_speaking, has_started_speaking
        if ev.new_state == "speaking":
            is_user_speaking = True
            has_started_speaking = True
            first_activity_event.set()  # real audio from callee
            reset_activity()
            if silence_trigger_task and not silence_trigger_task.done():
                silence_trigger_task.cancel()
        else:
            is_user_speaking = False

    @session.on("agent_state_changed")
    def on_agent_state_changed(ev):
        nonlocal is_agent_speaking, has_started_speaking
        if ev.new_state == "speaking":
            is_agent_speaking = True
            has_started_speaking = True
            first_activity_event.set()  # agent started speaking — call is live
            reset_activity()
            if silence_trigger_task and not silence_trigger_task.done():
                silence_trigger_task.cancel()
        else:
            is_agent_speaking = False

    @session.on("user_input_transcribed")
    def on_user_input(ev):
        nonlocal has_started_speaking
        has_started_speaking = True
        first_activity_event.set()
        if silence_trigger_task and not silence_trigger_task.done():
            silence_trigger_task.cancel()
        reset_activity()

    @session.on("conversation_item_added")
    def on_item_added(ev):
        nonlocal has_started_speaking
        has_started_speaking = True
        first_activity_event.set()
        if silence_trigger_task and not silence_trigger_task.done():
            silence_trigger_task.cancel()
        reset_activity()

    async def inactivity_monitor():
        try:
            if call_context.is_outbound:
                # Don't count ringing time — wait until the callee actually picks up
                # and audio starts flowing before we start the silence clock.
                await first_activity_event.wait()
                reset_activity()  # fresh baseline from first real audio
            # Grace period after call connects
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

    # Greet user at startup
    if not call_context.is_outbound:
        if "3.1" not in model:
            await asyncio.sleep(0.75)  # Wait for connection clicks/pops to settle
            await session.generate_reply(
                instructions=f"[System Note: Introduce yourself to the customer with this greeting: '{initial_greeting}']"
            )
    else:
        logger.info("Outbound call: waiting for callee to speak first.")


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
