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
LIVEKIT_AGENT_NAME=outbound-caller-local uv run agent.py start
# or
LIVEKIT_AGENT_NAME=outbound-caller-local .venv/bin/python agent.py start
```
The worker registers with LiveKit as the configured agent and exposes the LiveKit agents health server on `:8081` locally. Use distinct worker names per environment to avoid dispatch ambiguity: `outbound-caller-local`, `outbound-caller-dokploy`, and `outbound-caller-prod`.

### Running the Call-Control API
Hermes and other external agents trigger outbound PSTN calls through `call_api.py` instead of receiving LiveKit credentials directly. Run it separately from the worker:
```bash
set -a && source .env && set +a
LIVEKIT_AGENT_NAME=outbound-caller-local uv run uvicorn call_api:app --host 127.0.0.1 --port 8000
```
Use `--host 0.0.0.0` only when another device must reach the API over LAN/VPN/tunnel. Verify with:
```bash
curl -s http://127.0.0.1:8000/health
```
Open the local call-status dashboard after the API is running:
```bash
open http://127.0.0.1:8000/dashboard
```
Paste `CALL_API_TOKEN` in the dashboard; the page fetches protected data from `GET /dashboard/data`.

### Running the Web UI Sandbox Tester
```bash
uv run web_ui_server.py
# Open http://localhost:8080 in browser, select profile, connect, speak to test
```
The web UI requires the agent worker to be running separately (`uv run agent.py start`).

### Running Tests
Unit tests (no external services required):
```bash
.venv/bin/python -m unittest discover -s tests -p test_agent_tools.py -v
.venv/bin/python -m unittest discover -s tests -p test_agent_call_context.py -v
.venv/bin/python -m unittest discover -s tests -p test_web_ui.py -v
.venv/bin/python -m unittest discover -s tests -p test_call_outcomes.py -v
.venv/bin/python -m unittest discover -s tests -p test_call_api.py -v
.venv/bin/python -m unittest discover -s tests -p test_hermes_livekit_plugin.py -v
# Or discover all:
.venv/bin/python -m unittest discover -s tests
```

Root-level integration test (requires valid `.env` pointing to real Frappe):
```bash
.venv/bin/python -m unittest test_remote_agent.py
# or the frappe connectivity test:
.venv/bin/python -m unittest discover -s tests -p test_frappe_connection.py -v
```

Tests use Python's `unittest` framework (despite `pytest` in dev dependencies). Many tests heavily stub the livekit modules.

## Architecture

### Core Modules and Responsibilities

| File | Role |
|------|------|
| `agent.py` | LiveKit conversation worker entrypoint, call context detection, API-dial participant wait path, legacy worker-dial fallback, session setup, dynamic prompt compilation, DTMF listener for OTP |
| `agent_tools.py` | `CustomerQueryTools` (extends `llm.ToolContext`): all LLM function tools for customer lookups, WhatsApp OTP/PDF/text, `end_call` |
| `frappe_client.py` | `FrappeRestClient`: pure REST client using Token auth. Methods: `lookup_caller`, `get_resource`, `get_resource_list`, `send_whatsapp_message*` |
| `agent_config.json` | Runtime config: provider/model/voice, agent personas (prompts + direction-specific greetings), noise_cancellation, custom_tts |
| `web_ui_server.py` | Minimal HTTP server serving static web tester; mints LiveKit tokens and dispatches the agent into test rooms |
| `call_api.py` | FastAPI call-control service for Hermes/external AI: validates bearer auth, normalizes/limits phone numbers, persists call records, serves the dashboard, creates LiveKit rooms, dispatches the outbound worker, creates outbound SIP participants, and returns exact dial outcomes |
| `call_outcomes.py` | Shared SIP outcome mapping used by the API and worker (`486` → `busy`, `408` → `no_answer`, etc.) |
| `call_status_store.py` | SQLite persistence layer for outbound call records and event history (`call_control.sqlite3` by default; override with `CALL_API_DB_PATH`) |
| `call_dashboard.py` | Self-contained HTML/CSS/JS dashboard rendered by `GET /dashboard`; data loads from authenticated `GET /dashboard/data` |
| `integrations/hermes/livekit-caller/` | Hermes plugin exposing `make_phone_call` and `get_phone_call_status`; install under `~/.hermes/plugins/livekit-caller` and enable with `hermes plugins enable livekit-caller` |

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

**Outbound SIP Dialing and Status Tracking**: API-owned dialing is the default for Hermes/external outbound calls. `call_api.py` creates the LiveKit room, dispatches the selected worker, creates the SIP participant with `wait_until_answered=True`, maps exact SIP outcomes via `call_outcomes.py`, stores the result in SQLite, and returns the immediate status to the caller. The worker receives `outbound_dial_mode="api"`, waits for the API-created SIP participant to become active, starts the `AgentSession` linked to that participant, and does not place a second SIP call. `_ensure_outbound_participant` remains as a legacy/manual fallback for worker-owned dialing.

**Hermes Call Control**: `call_api.py` is the boundary for Hermes and other external AI agents. It requires `Authorization: Bearer <CALL_API_TOKEN>`, accepts `POST /calls`, creates a room named `agent_call_<call_id>`, embeds outbound metadata (phone, purpose, requested_by, agent_type, `outbound_dial_mode`, `sip_participant_identity`), dispatches the LiveKit worker, dials the PSTN leg, persists status in SQLite, and returns the immediate setup/dial result (`answered`, `failed_busy`, `failed_no_answer`, `failed_unreachable`, `failed_rejected`, `failed_trunk`, or `failed`). Hermes only needs `LIVEKIT_CALL_API_URL` and `LIVEKIT_CALL_API_TOKEN`; it must not receive `LIVEKIT_API_SECRET`.

**Call Status Dashboard**: `GET /dashboard` serves a browser UI with summary cards, recent calls, SIP status, and per-call event timelines. The UI asks for `CALL_API_TOKEN` and fetches data from authenticated `GET /dashboard/data`. This is intended for local/operator visibility; do not expose it publicly without HTTPS and the same bearer-auth protections as `/calls`.

**Transcript and Recording Sources**: Transcript/session-report data should come from LiveKit (for example the LiveKit Agents session report/history posted back by the worker after call end). The operator-facing call recording URL should come from the Vobiz API, not LiveKit egress, unless this decision is explicitly changed. Vobiz credentials must stay on the Call API side; Hermes should only receive Call API URL/token values.

**Hermes Plugin Lifecycle**: Hermes discovers user plugins from `~/.hermes/plugins/<name>/`, not arbitrary repo-relative paths unless project plugins are explicitly enabled. Copy or symlink `integrations/hermes/livekit-caller` to `~/.hermes/plugins/livekit-caller`, run `hermes plugins enable livekit-caller`, configure `LIVEKIT_CALL_API_URL` / `LIVEKIT_CALL_API_TOKEN`, then restart Hermes or start a new session before expecting `make_phone_call` and `get_phone_call_status` to appear.

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
2. `call_api.py` authenticates `CALL_API_TOKEN`, validates allowed country prefixes, creates the SQLite call record, creates a LiveKit room, and dispatches the worker selected by `LIVEKIT_AGENT_NAME`
3. `call_api.py` creates the outbound SIP participant using `OUTBOUND_TRUNK_ID` (`ST_...` trunk ID) with `wait_until_answered=True`
4. `call_api.py` records and returns the exact setup result (`answered` or a structured `failed_*` status with raw SIP code/text when available)
5. `agent.py` receives metadata with `outbound_dial_mode="api"`, resolves outbound call context, optionally looks up the phone in Frappe, waits for the API-created SIP participant to become active, and starts the realtime conversation worker linked to that participant
6. After the callee answers and speaks first, the realtime agent responds; Hermes can call `get_phone_call_status(call_id)` or an operator can open `/dashboard` to inspect the latest status and event timeline

## Configuration

**Environment variables** (see `.env.example`):
- LiveKit: `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`
- Worker selection: `LIVEKIT_AGENT_NAME` (recommended values: `outbound-caller-local`, `outbound-caller-dokploy`, `outbound-caller-prod`)
- Frappe: `FRAPPE_SITE_URL`, `FRAPPE_API_KEY`, `FRAPPE_API_SECRET`
- AI: `GOOGLE_API_KEY` or `OPENAI_API_KEY`
- Telephony (outbound): `OUTBOUND_TRUNK_ID`, `VOBIZ_SIP_DOMAIN`, `DEFAULT_TRANSFER_NUMBER`
- Recording lookup: Vobiz recording API endpoint/credentials when implemented; keep them non-committed and expose only derived recording URLs through the Call API
- Call-control API: `CALL_API_TOKEN`, `CALL_API_ALLOWED_COUNTRY_PREFIXES`, `CALL_API_DEFAULT_COUNTRY_CODE`, `CALL_API_MAX_PURPOSE_CHARS`, optional `CALL_API_DB_PATH` / `CALL_STATUS_DB_PATH`
- Hermes plugin/client: `LIVEKIT_CALL_API_URL`, `LIVEKIT_CALL_API_TOKEN` (same value as `CALL_API_TOKEN` on the API service)

`OUTBOUND_TRUNK_ID` must be the LiveKit SIP trunk ID from LiveKit Cloud → Telephony → SIP trunks (for example `ST_...`), **not** a phone number. Restart the API process after changing `OUTBOUND_TRUNK_ID`, `LIVEKIT_AGENT_NAME`, or LiveKit credentials used by API-owned dialing. Restart the worker after changing worker-consumed `.env` values.

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
- For local call-control testing, run both the worker (`LIVEKIT_AGENT_NAME=outbound-caller-local uv run agent.py start`) and API (`LIVEKIT_AGENT_NAME=outbound-caller-local uv run uvicorn call_api:app --host 127.0.0.1 --port 8000`) with `.env` loaded.
- With API-owned dialing, `POST /calls` is intentionally blocking until answer/failure and returns the immediate SIP setup outcome. Keep client timeouts long enough for ringing/no-answer.
- The call API owns final SIP failure status. Worker disconnect handlers should not overwrite `failed_*` records with `completed` after a rejected/busy/no-answer leg.
- The call API stores SQLite status. Locally this is the repo-root `call_control.sqlite3`; in separate containers/hosts or LiveKit Cloud worker deployments, use the API/dashboard store as the source of truth and add a networked store/callback before relying on worker-side status writes.
- Transcript/recording storage is implemented in the Call API status database: the worker posts LiveKit session history to `/internal/calls/{call_id}/session-report`, and recording URLs come from Vobiz API callback/polling fields. Store large transcript/recording payloads outside SQLite if they grow; SQLite keeps source fields and pointers/URLs.
- `/calls/{call_id}` returns the persisted record plus event history. `/dashboard/data` returns recent records and summary counts. Both require bearer auth; `/dashboard` serves only the static UI shell.
- To stop local call-control services, terminate the terminals or run `pkill -f "uvicorn call_api:app"` and `pkill -f "agent.py start"`.
- SIP failure `object cannot be found` usually means `OUTBOUND_TRUNK_ID` is wrong; SIP `486 Busy Here` maps to `failed_busy`; `480 Temporarily Unavailable` maps best-effort to `failed_unreachable`; `408 Request Timeout` maps to `failed_no_answer`. Carriers do not always distinguish switched-off vs out-of-coverage.
- Frappe lookup failures (for example local `127.0.0.1:8002` refused) fall back to an unknown lead and do not by themselves block outbound dialing.

## Deployment

Dockerfile uses `uv` for install and runs `uv run agent.py start`. The worker app does not need inbound ports (it connects outbound to LiveKit). The call-control API is a separate service/process using `uv run uvicorn call_api:app --host 0.0.0.0 --port 8000` and should be exposed only behind HTTPS, bearer auth, and tight country-prefix restrictions. For LiveKit Cloud production, deploy the worker as `outbound-caller-prod` and configure the API service to dispatch that same `LIVEKIT_AGENT_NAME`; LiveKit Cloud injects worker LiveKit credentials, but the API service still needs LiveKit credentials because it creates rooms, dispatches workers, and creates SIP participants. See `docs/dokploy.md` and `docs/hermes-call-control.md` for deployment metadata and MCP-first redeploy workflow if `dokploy-mcp` is configured.
