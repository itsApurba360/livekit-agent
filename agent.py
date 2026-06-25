# -*- coding: utf-8 -*-
import os
import certifi

# Fix for macOS SSL Certificate errors
os.environ['SSL_CERT_FILE'] = certifi.where()

import logging
import json
from dotenv import load_dotenv
from livekit import agents, api
from livekit.agents import AgentSession, Agent, RoomInputOptions
from livekit.agents.beta.tools import EndCallTool
from livekit.plugins import openai, google, noise_cancellation
from typing import Optional

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

DEFAULT_SUPPORT_UNVERIFIED_RULES = """- The caller is linked to a registered customer number. You may answer questions about sales orders, invoice status, outstanding amounts, and customer info directly using your tools (get_customer_sales_orders, get_customer_pending_amount, get_customer_details, get_sales_order_details, get_sales_invoice_details) without any WhatsApp verification.
- Do NOT ask for verification at the start of the call or for voice-only queries.
- WhatsApp verification is ONLY required when the customer asks to receive information via WhatsApp (text details such as order ID, balance, customer name, or a PDF copy of a Sales Order or Sales Invoice).
- When the customer asks for WhatsApp delivery, you MUST call `send_verification_otp` immediately. Do not ask for permission first.
- CRITICAL: You are NOT allowed to say you sent a code without executing `send_verification_otp` first. Only after the tool returns success, say: "मैंने आपके व्हाट्सएप पर एक वेरिफिकेशन कोड भेजा है। कृपया मुझे वह कोड बताएं या अपने फोन कीपैड पर टाइप करें।"
- When they speak the code, call `verify_otp` to check if it matches. Only after successful verification, call `send_text_whatsapp` for text details or `send_pdf_whatsapp` for PDF documents.
- If the customer is not registered on WhatsApp (indicated by the `send_verification_otp` tool), ask them to provide a valid WhatsApp number."""

DEFAULT_SUPPORT_VERIFIED_RULES = """- The customer is verified for WhatsApp delivery. You may call `send_text_whatsapp` to send requested details as a text message, or `send_pdf_whatsapp` to send a Sales Order or Sales Invoice PDF.
- Do NOT repeat or mention verification, OTP, or verification status in subsequent turns. Continue helping with their request.
- You may still use all customer query tools freely for voice answers."""

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
    logger.info(f"Connecting to room: {ctx.room.name}")

    # Initialize remote Frappe REST client
    frappe_url = os.environ.get("FRAPPE_SITE_URL")
    api_key = os.environ.get("FRAPPE_API_KEY")
    api_secret = os.environ.get("FRAPPE_API_SECRET")
    client = FrappeRestClient(base_url=frappe_url, api_key=api_key, api_secret=api_secret)

    phone_number = None
    config_dict = {}

    # 1. Parse Job Metadata
    try:
        if ctx.job.metadata:
            data = json.loads(ctx.job.metadata)
            phone_number = data.get("phone_number")
            config_dict = data
    except Exception:
        pass

    # 2. Parse Room Metadata
    try:
        if ctx.room.metadata:
            data = json.loads(ctx.room.metadata)
            if data.get("phone_number"):
                phone_number = data.get("phone_number")
            config_dict.update(data)
    except Exception:
        logger.warning("No valid JSON metadata found in Room.")

    # 3. Detect Inbound SIP Participant Caller ID
    is_inbound_call = not ctx.room.name.startswith("agent_call_")
    if not phone_number and is_inbound_call:
        logger.info("Inbound call detected. Waiting for SIP participant to populate...")
        import asyncio
        for attempt in range(30):
            for p in ctx.room.remote_participants.values():
                if p.identity.startswith("sip_"):
                    phone_number = p.identity.replace("sip_", "")
                    logger.info(f"Detected inbound SIP caller from participant identity on attempt {attempt + 1}: {phone_number}")
                    break
            if phone_number:
                break
            await asyncio.sleep(0.1)

    if not phone_number:
        # Fallback parsing from room name (e.g. 919876543210_room)
        parts = ctx.room.name.split("_")
        if parts and parts[0].isdigit() and len(parts[0]) >= 10:
            phone_number = parts[0]
            logger.info(f"Detected caller phone number from room name: {phone_number}")

    # 4. Perform Lookup of Caller via REST Client
    caller_info = {"status": "Unknown", "name": "जी", "company": "हमारी कंपनी"}
    if phone_number:
        try:
            logger.info(f"Performing remote lookup for caller phone: {phone_number}")
            caller_info = client.lookup_caller(phone_number)
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
    initial_greeting_template = agent_settings.get("initial_greeting", fallback_greeting)

    # Resolve dynamic system prompt compilation
    def get_compiled_prompt(is_verified: bool = False) -> str:
        nonlocal system_prompt_template, lead_name, company_name, customer_id
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
            prompt_base += f"\n\nThe caller is linked to Customer ID: '{customer_id}'. You can use tools (get_customer_sales_orders, get_sales_order_details, get_customer_pending_amount, get_sales_invoice_details, get_customer_details, send_text_whatsapp, send_pdf_whatsapp) to query their details. When explaining details, summarize in natural, polite Hindi/Hinglish. Do not read raw codes verbatim."
        else:
            prompt_base += "\n\nNo Customer is currently linked to this call. You can use the search_customer tool to find a customer by name or phone number."

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
        on_verify_success=on_verification_success
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
    elif provider == "OpenAI":
        realtime_llm = openai.realtime.RealtimeModel(
            model=model,
            voice=voice.lower() if voice else "alloy",
            api_key=ai_api_key,
        )
    else:
        raise ValueError(f"Unsupported provider: {provider}")

    session = AgentSession(llm=realtime_llm)
    fnc_ctx.session = session

    # Initialize prebuilt call termination tool
    end_call_tool = EndCallTool(
        delete_room=True,
        end_instructions="Politely say goodbye to the user in simple Hindi/Hinglish (e.g., 'अलविदा, धन्यवाद!')"
    )
    agent_tools = list(fnc_ctx.function_tools.values()) + list(end_call_tool.tools)

    # Start LiveKit Agent Session
    agent_instance = StandaloneAgent(instructions=system_prompt, tools=agent_tools)
    await session.start(
        room=ctx.room,
        agent=agent_instance,
        room_input_options=RoomInputOptions(
            noise_cancellation=noise_cancellation.BVCTelephony(),
            close_on_disconnect=True,
        ),
    )

    # Greet user at startup
    if "3.1" not in model:
        await session.generate_reply(
            instructions=f"[System Note: Introduce yourself to the customer with this greeting: '{initial_greeting}']"
        )


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
            agent_name="remote-agent-worker",
            load_threshold=0.99,
        )
    )
