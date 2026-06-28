# AGENTS.md

This file provides guidance to WARP (warp.dev) when working with code in this repository.

## Project Overview

Standalone REST-decoupled LiveKit Voice AI Agent for Frappe/ERPNext. The agent runs as a pure Python LiveKit worker that communicates with any remote Frappe/ERPNext instance exclusively via HTTP REST API (no local Frappe imports or bench coupling).

Two agent personas are supported, selected at runtime based on caller lookup:
- **Support (Kavya)**: For existing customers. Can query orders, invoices, balances.
- **Sales (Nandini)**: For leads/unknown callers. Qualifies requirements.

## Development Commands

### Environment Setup
```bash
uv sync
cp .env.example .env
# Edit .env with LIVEKIT_*, FRAPPE_*, and GOOGLE_API_KEY or OPENAI_API_KEY
```

### Running the Agent Worker
```bash
uv run agent.py start
# or
.venv/bin/python agent.py start
```
The worker registers with LiveKit as the configured agent (currently `outbound-caller`) and exposes the LiveKit agents health server on `:8081` locally.

### Running the Call-Control API
Hermes and other external agents trigger outbound PSTN calls through `call_api.py` instead of receiving LiveKit credentials directly. Run it separately from the worker:
```bash
set -a && source .env && set +a
uv run uvicorn call_api:app --host 127.0.0.1 --port 8000
```
Use `--host 0.0.0.0` only when another device must reach the API over LAN/VPN/tunnel. Verify with:
```bash
curl -s http://127.0.0.1:8000/health
```

### Running the Web UI Sandbox Tester
```bash
uv run web_ui_server.py
# Open http://localhost:8080 in browser, select profile, connect, speak to test
```
The web UI requires the agent worker to be running separately (`uv run agent.py start`).

### Running Tests
Unit tests (no external services required):
```bash
.venv/bin/python -m unittest tests.test_agent_tools
.venv/bin/python -m unittest tests.test_agent_call_context
.venv/bin/python -m unittest tests.test_web_ui
.venv/bin/python -m unittest discover -s tests -p test_call_api.py
.venv/bin/python -m unittest discover -s tests -p test_hermes_livekit_plugin.py
# Or discover all:
.venv/bin/python -m unittest discover -s tests
```

Root-level integration test (requires valid `.env` pointing to real Frappe):
```bash
.venv/bin/python -m unittest test_remote_agent.py
# or the frappe connectivity test:
.venv/bin/python -m unittest tests.test_frappe_connection
```

Tests use Python's `unittest` framework (despite `pytest` in dev dependencies). Many tests heavily stub the livekit modules.

## Architecture

### Core Modules and Responsibilities

| File | Role |
|------|------|
| `agent.py` | LiveKit entrypoint, call context detection, SIP outbound dialing, session setup, dynamic prompt compilation, DTMF listener for OTP |
| `agent_tools.py` | `CustomerQueryTools` (extends `llm.ToolContext`): all LLM function tools for customer lookups, WhatsApp OTP/PDF/text, `end_call` |
| `frappe_client.py` | `FrappeRestClient`: pure REST client using Token auth. Methods: `lookup_caller`, `get_resource`, `get_resource_list`, `send_whatsapp_message*` |
| `agent_config.json` | Runtime config: provider/model/voice, agent personas (prompts + direction-specific greetings), noise_cancellation, custom_tts |
| `web_ui_server.py` | Minimal HTTP server serving static web tester; mints LiveKit tokens and dispatches the agent into test rooms |
| `call_api.py` | FastAPI call-control service for Hermes/external AI: validates bearer auth, normalizes/limits phone numbers, creates LiveKit rooms, and dispatches the outbound worker |
| `integrations/hermes/livekit-caller/` | Hermes plugin exposing `make_phone_call`; install under `~/.hermes/plugins/livekit-caller` and enable with `hermes plugins enable livekit-caller` |

### Key Architectural Patterns

**REST Decoupling**: All Frappe interaction goes through `FrappeRestClient`. No `frappe` or `erpnext` Python packages are imported. The client uses standard Token auth (`token {api_key}:{api_secret}`) and calls `/api/resource/...` and whitelisted `/api/method/...` endpoints (for WhatsApp via `watoolx_whatsapp`).

**Call Context and Direction**: `CallContext` is built from multiple sources (priority order):
1. Job metadata + room metadata
2. SIP participant attributes (`sip.phoneNumber`, `sip.ruleID`, etc.)
3. Room name patterns (`agent_call_*` → outbound; phone prefix in room name → inbound)
Direction determines greeting selection and behavioral rules injected into the system prompt.

**Dynamic Prompt Compilation (Support Agent)**: The support agent's system prompt includes a `{verification_rules}` placeholder. At runtime, `get_compiled_prompt(is_verified=...)` injects either `DEFAULT_SUPPORT_UNVERIFIED_RULES` or `DEFAULT_SUPPORT_VERIFIED_RULES`. On successful WhatsApp OTP verification, the chat context's system message is swapped in-place and (for non-Gemini) `session.generate_reply()` is triggered.

**Verification Flow for WhatsApp Delivery**:
- Voice queries (orders, balances, details) work without verification.
- WhatsApp delivery (`send_text_whatsapp`, `send_pdf_whatsapp`) requires prior `send_verification_otp` + `verify_otp` (spoken or DTMF via `sip_dtmf_received`).
- On success, `on_verification_success` callback updates the live prompt and confirms via a one-time system note.

**Outbound SIP Dialing**: For outbound calls, `_ensure_outbound_participant` uses `ctx.api.sip.create_sip_participant` (with `wait_until_answered=True`) before starting the `AgentSession`. The callee must speak first; the agent waits.

**Hermes Call Control**: `call_api.py` is the boundary for Hermes and other external AI agents. It requires `Authorization: Bearer $CALL_API_TOKEN`, accepts `POST /calls`, creates a room named `agent_call_<call_id>`, embeds outbound metadata (phone, purpose, requested_by, agent_type), and dispatches the LiveKit worker. Hermes only needs `LIVEKIT_CALL_API_URL` and `LIVEKIT_CALL_API_TOKEN`; it must not receive `LIVEKIT_API_SECRET`.

**Hermes Plugin Lifecycle**: Hermes discovers user plugins from `~/.hermes/plugins/<name>/`, not arbitrary repo-relative paths unless project plugins are explicitly enabled. Copy or symlink `integrations/hermes/livekit-caller` to `~/.hermes/plugins/livekit-caller`, run `hermes plugins enable livekit-caller`, configure `LIVEKIT_CALL_API_URL` / `LIVEKIT_CALL_API_TOKEN`, then restart Hermes or start a new session before expecting `make_phone_call` to appear.

**Provider Flexibility**: Supports `Google` (Gemini realtime) and `OpenAI` (realtime). OpenAI can optionally use a custom Gemini TTS via `google.beta.GeminiTTS` while keeping OpenAI for the LLM. Provider/model/voice are read from `agent_config.json` at startup.

**Tool Context Lifecycle**: `CustomerQueryTools` holds references to `client`, `session`, and `ctx` (JobContext). Tools are async and use `asyncio.to_thread` for blocking REST calls. `end_call` triggers a delayed `ctx.delete_room()` + `ctx.shutdown()` or `session.shutdown()`.

### Data Flow (Inbound Example)
1. LiveKit dispatches job → `entrypoint(ctx)`
2. Parse metadata → `_build_call_context` (direction, phone)
3. Optional: wait for SIP participant attributes
4. `client.lookup_caller(phone)` → determines "Customer" vs "Lead" → selects agent persona
5. Compile system prompt with caller info + call context
6. Initialize provider realtime model + `AgentSession`
7. Start session, optionally greet (skipped for outbound)
8. Tools become available to the LLM; verification state mutates prompt if needed

### Data Flow (Hermes-Initiated Outbound Example)
1. Hermes calls `make_phone_call` → plugin posts to `call_api.py` `/calls`
2. `call_api.py` authenticates `CALL_API_TOKEN`, validates allowed country prefixes, creates a LiveKit room, and dispatches `outbound-caller`
3. `agent.py` reads metadata, resolves outbound call context, optionally looks up the phone in Frappe, and calls `_ensure_outbound_participant`
4. LiveKit SIP sends the PSTN INVITE using `OUTBOUND_TRUNK_ID` (`ST_...` trunk ID)
5. After the callee answers and speaks first, the realtime agent starts the voice conversation

## Configuration

**Environment variables** (see `.env.example`):
- LiveKit: `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`
- Frappe: `FRAPPE_SITE_URL`, `FRAPPE_API_KEY`, `FRAPPE_API_SECRET`
- AI: `GOOGLE_API_KEY` or `OPENAI_API_KEY`
- Telephony (outbound): `OUTBOUND_TRUNK_ID`, `VOBIZ_SIP_DOMAIN`, `DEFAULT_TRANSFER_NUMBER`
- Call-control API: `CALL_API_TOKEN`, `CALL_API_ALLOWED_COUNTRY_PREFIXES`, `CALL_API_DEFAULT_COUNTRY_CODE`, `CALL_API_MAX_PURPOSE_CHARS`
- Hermes plugin/client: `LIVEKIT_CALL_API_URL`, `LIVEKIT_CALL_API_TOKEN` (same value as `CALL_API_TOKEN` on the API service)

`OUTBOUND_TRUNK_ID` must be the LiveKit SIP trunk ID from LiveKit Cloud → Telephony → SIP trunks (for example `ST_...`), **not** a phone number. Restart the worker after changing any `.env` value it consumes.

**agent_config.json** (committed):
- `agent_type`, `provider`, `model`, `voice`
- `noise_cancellation`, `custom_tts`
- `support_agent` / `sales_agent`: `name`, direction-specific greetings (`inbound_initial_greeting`, `outbound_initial_greeting`, `initial_greeting`), `system_prompt`
- System prompts use `{lead_name}`, `{company_name}`, `{verification_rules}` placeholders

## Important Implementation Notes

- Greeting selection uses `_select_initial_greeting_template` which prefers direction-specific keys.
- Sales inbound greetings must not assume the agent called the user (see tests for assertions).
- For outbound, the first LLM response is not auto-generated; the agent waits for callee speech.
- DTMF handling for OTP bypasses the normal LLM path and directly feeds the 4-digit buffer.
- PDF sending resolves a print format (prefers "Sales Order with payment details" when a Payment Entry exists) and constructs a Frappe download URL.
- All tool responses are natural language strings intended for voice; raw JSON/IDs are summarized.
- For local call-control testing, run both the worker (`uv run agent.py start`) and API (`uv run uvicorn call_api:app --host 127.0.0.1 --port 8000`) with `.env` loaded.
- To stop local call-control services, terminate the terminals or run `pkill -f "uvicorn call_api:app"` and `pkill -f "agent.py start"`.
- SIP failure `object cannot be found` usually means `OUTBOUND_TRUNK_ID` is wrong; SIP `486 Busy Here` means the trunk/carrier/phone rejected the INVITE even though dispatch worked.
- Frappe lookup failures (for example local `127.0.0.1:8002` refused) fall back to an unknown lead and do not by themselves block outbound dialing.

## Deployment

Dockerfile uses `uv` for install and runs `uv run agent.py start`. The worker app does not need inbound ports (it connects outbound to LiveKit). The call-control API is a separate service/process using `uv run uvicorn call_api:app --host 0.0.0.0 --port 8000` and should be exposed only behind HTTPS, bearer auth, and tight country-prefix restrictions. See `docs/dokploy.md` and `docs/hermes-call-control.md` for deployment metadata and MCP-first redeploy workflow if `dokploy-mcp` is configured.
