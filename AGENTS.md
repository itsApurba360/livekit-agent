# AGENTS.md

This file provides guidance to AI coding agents working in this repository.

## Project Overview

Standalone REST-decoupled LiveKit Voice AI Agent for Frappe/ERPNext. The worker is a pure Python LiveKit service and communicates with remote Frappe/ERPNext only through HTTP REST APIs (no local Frappe imports or bench coupling).

Current scope is outbound-first:

- **Call API (`call_api.py`)** owns Hermes/external requests, room creation, worker dispatch, outbound SIP dialing, status persistence, dashboard data, recording proxy/lookup, and Google Sheets automation controls.
- **Worker (`agent.py`)** owns live conversation after the API-created PSTN participant answers.
- **Google Sheets automation (`sheet_calling_automation.py`)** can run GST/document-collection campaigns by reading pending sheet rows, posting `/calls`, and syncing completed outcomes back to the sheet.

Two personas are still supported by runtime selection:

- **Support (Kavya)**: For existing customers. Can query orders, invoices, balances, and in GST/document campaigns schedules human callbacks instead of collecting documents directly.
- **Sales (Nandini)**: For leads/unknown callers. Qualifies requirements.

## Development Commands

### Environment Setup

```bash
uv sync
cp .env.example .env
# Edit .env with LIVEKIT_*, FRAPPE_*, CALL_API_*, and GOOGLE_API_KEY or OPENAI_API_KEY.
```

For Google Sheets campaign automation also configure:

```bash
GOOGLE_SHEETS_SPREADSHEET_ID=<spreadsheet-id>
GOOGLE_SHEETS_CREDS_PATH=.google_sheets_creds.json
```

Never commit `.env`, Google service-account JSON, runtime flag files, or logs.

### Running the Agent Worker

> [!NOTE]
> The Agent Worker (`agent.py`) is deployed and hosted on **LiveKit Cloud** (agent ID: `CA_ct6s7UGyzoju`). **No local worker process needs to be run locally going forward.** All environments dispatch and connect to this cloud worker (`outbound-caller-prod`).


Hermes, the dashboard, and sheet automation trigger outbound PSTN calls through `call_api.py`. Run the Call API:

```bash
set -a && source .env && set +a
LIVEKIT_AGENT_NAME=outbound-caller-prod uv run uvicorn call_api:app --host 127.0.0.1 --port 8000
```

Use `--host 0.0.0.0` only when another device/service must reach the API over LAN/VPN/tunnel and the API is protected by HTTPS/bearer auth. Verify with:

```bash
curl -s http://127.0.0.1:8000/health
```

Open the local dashboard:

```bash
open http://127.0.0.1:8000/dashboard
```

Paste `CALL_API_TOKEN`; the page fetches protected data from `GET /dashboard/data`, supports inline recording playback, and exposes Start Agent / Kill Switch controls for the Google Sheets automation loop.

### Running Google Sheets Automation

One cycle:

```bash
set -a && source .env && set +a
uv run sheet_calling_automation.py
```

Dashboard-managed loop:

- `POST /agent/start` starts the loop inside the Call API process and repeats roughly every 15 seconds.
- `POST /agent/kill` writes `agent_stop.flag`, removes `agent_running.flag`, deletes active LiveKit rooms tracked in PostgreSQL, and marks active calls killed.
- `POST /calls/{call_id}/kill` terminates one call room.

See `docs/google-sheets-calling-automation.md` for sheet schema and operational rules.

### Running the Web UI Sandbox Tester

```bash
uv run web_ui_server.py
# Open http://localhost:8080 in browser, select profile, connect, speak to test.
```

The web UI connects to the LiveKit Cloud project where the hosted agent worker handles the session.

### Running Tests

Targeted unit tests (no external services required):

```bash
.venv/bin/python -m unittest discover -s tests -p test_agent_tools.py -v
.venv/bin/python -m unittest discover -s tests -p test_agent_call_context.py -v
.venv/bin/python -m unittest discover -s tests -p test_web_ui.py -v
.venv/bin/python -m unittest discover -s tests -p test_call_outcomes.py -v
.venv/bin/python -m unittest discover -s tests -p test_call_api.py -v
.venv/bin/python -m unittest discover -s tests -p test_hermes_livekit_plugin.py -v
.venv/bin/python -m unittest discover -s tests -p test_sheet_automation.py -v
```

Full discovery may include integration-style tests under `tests/` that require a reachable Frappe site (for example `127.0.0.1:8002` in local `.env`). Run it only when those services are available:

```bash
.venv/bin/python -m unittest discover -s tests
```

Root-level and Frappe integration tests require valid `.env` values pointing to real services:

```bash
.venv/bin/python -m unittest test_remote_agent.py
.venv/bin/python -m unittest discover -s tests -p test_frappe_connection.py -v
```

Tests use Python's `unittest` framework despite `pytest` in dev dependencies. Many tests heavily stub LiveKit modules.

## Architecture

### Core Modules and Responsibilities

| File | Role |
|------|------|
| `agent.py` | LiveKit conversation worker entrypoint, call context detection, API-dial participant wait path, legacy worker-dial fallback, session setup, dynamic prompt compilation, GST/document campaign rules, silence timeout handling, DTMF listener for OTP |
| `agent_tools.py` | `CustomerQueryTools` (extends `llm.ToolContext`): all LLM function tools for customer lookups, WhatsApp OTP/PDF/text, `schedule_human_callback`, `end_call` |
| `frappe_client.py` | `FrappeRestClient`: pure REST client using Token auth. Methods: `lookup_caller`, `get_resource`, `get_resource_list`, `send_whatsapp_message*` |
| `agent_config.json` | Runtime config: provider/model/voice, agent personas (prompts + direction-specific greetings), noise_cancellation, custom_tts |
| `web_ui_server.py` | Minimal HTTP server serving static web tester; mints LiveKit tokens and dispatches the agent into test rooms |
| `call_api.py` | FastAPI call-control service: validates bearer auth, normalizes/limits phone numbers, persists call records, serves dashboard/API data, creates LiveKit rooms, dispatches workers, creates outbound SIP participants, maps dial outcomes, proxies recordings, accepts session reports, and starts/kills sheet automation |
| `call_outcomes.py` | Shared SIP outcome mapping used by the API and worker (`486` → `busy`, `408` → `no_answer`, etc.) |
| `call_status_store.py` | PostgreSQL persistence layer for outbound call records, event history, transcript/session-report fields, Vobiz recording fields, and metadata. Requires `CALL_API_DATABASE_URL` or `CALL_STATUS_DATABASE_URL`. |
| `call_dashboard.py` | Self-contained HTML/CSS/JS dashboard rendered by `GET /dashboard`; data loads from authenticated JSON endpoints; inline recording playback uses `/calls/{call_id}/recording` |
| `sheet_calling_automation.py` | Google Sheets campaign loop: reads pending rows, calls `/calls`, prevents duplicate active dials, syncs completed call outcomes/transcripts/recording links to Sheet 2, updates Sheet 1 comment/count |
| `vobiz_client.py` | Vobiz REST client for recording metadata lookup and recording media proxy streaming/range support |
| `integrations/hermes/livekit-caller/` | Hermes plugin exposing `make_phone_call` and `get_phone_call_status`; install under `~/.hermes/plugins/livekit-caller` and enable with `hermes plugins enable livekit-caller` |

### Key Architectural Patterns

**REST Decoupling**: All Frappe interaction goes through `FrappeRestClient`. No `frappe` or `erpnext` Python packages are imported. The client uses standard Token auth (`token {api_key}:{api_secret}`) and calls `/api/resource/...` and whitelisted `/api/method/...` endpoints.

**Call Context and Direction**: `CallContext` is built from metadata/room metadata first, SIP participant attributes second, and room name patterns third (`agent_call_*` → outbound; phone prefix in room name → inbound). Direction determines greeting selection and behavioral rules injected into the system prompt.

**Dynamic Prompt Compilation**: Support prompts include `{verification_rules}`. At runtime, `get_compiled_prompt(is_verified=...)` injects unverified/verified WhatsApp delivery rules. For outbound purposes containing `gst`, `document`, `filing`, or `pdf`, `agent.py` appends GST/document campaign rules that route the conversation toward a human callback instead of OTP/document collection.

**Verification Flow for WhatsApp Delivery**:

- Voice queries (orders, balances, details) work without verification.
- WhatsApp delivery (`send_text_whatsapp`, `send_pdf_whatsapp`) requires `send_verification_otp` + `verify_otp` (spoken or DTMF via `sip_dtmf_received`).
- GST/document collection campaigns explicitly tell the agent not to ask for OTP/WhatsApp verification.
- On verification success, the live prompt is updated and a one-time system note can trigger confirmation.

**Outbound SIP Dialing and Status Tracking**: API-owned dialing is the default. `call_api.py` creates the LiveKit room, dispatches the selected worker, creates the SIP participant with `wait_until_answered=True`, maps exact SIP outcomes via `call_outcomes.py`, stores status in PostgreSQL, and returns the immediate setup result. The worker receives `outbound_dial_mode="api"`, waits for the API-created SIP participant to become active, starts the `AgentSession` linked to that participant, and does not place a second SIP call. `_ensure_outbound_participant` remains as a legacy/manual fallback for worker-owned dialing.

**Hermes Call Control**: `call_api.py` is the boundary for Hermes and other external AIs. It requires `Authorization: Bearer <CALL_API_TOKEN>`, accepts `POST /calls`, creates a room named `agent_call_<call_id>`, embeds outbound metadata, dispatches the worker, dials the PSTN leg, persists status in PostgreSQL, and returns the immediate setup/dial result (`answered`, `failed_busy`, `failed_no_answer`, `failed_unreachable`, `failed_rejected`, `failed_trunk`, or `failed`). Hermes only needs `LIVEKIT_CALL_API_URL` and `LIVEKIT_CALL_API_TOKEN`; it must not receive `LIVEKIT_API_SECRET`.

**Call Status Dashboard**: `GET /dashboard` serves a browser UI with summary cards, recent calls, SIP status, per-call timelines, inline recording playback, recording refresh, Start Agent, Kill Switch, and per-call kill controls. The UI asks for `CALL_API_TOKEN` and fetches protected data from JSON endpoints. Do not expose the API publicly without HTTPS and the same bearer-auth protections as `/calls`.

**Transcript and Recording Sources**: Transcript/session-report data comes from LiveKit. The worker posts session history to `/internal/calls/{call_id}/session-report`; the handler stores transcript/report data and triggers Vobiz recording lookup. Operator-facing recording URLs come from Vobiz, and playback goes through `GET /calls/{call_id}/recording` so the browser never needs Vobiz credentials. Hermes should only receive Call API URL/token values.

**Google Sheets Campaign Automation**: `sheet_calling_automation.py` reads Sheet 1 pending rows, posts `/calls` with `requested_by="sheets_automation"`, stores `cid/source` in call metadata, syncs completed calls back to Sheet 2, and marks metadata `synced=True`. `schedule_human_callback` writes `next_action`, callback date/time, and client notes into PostgreSQL metadata for the sync step.

**Hermes Plugin Lifecycle**: Hermes discovers user plugins from `~/.hermes/plugins/<name>/`, not arbitrary repo-relative paths unless project plugins are explicitly enabled. Copy or symlink `integrations/hermes/livekit-caller` to `~/.hermes/plugins/livekit-caller`, run `hermes plugins enable livekit-caller`, configure `LIVEKIT_CALL_API_URL` / `LIVEKIT_CALL_API_TOKEN`, then restart Hermes or start a new session before expecting `make_phone_call` and `get_phone_call_status` to appear.

**Provider Flexibility**: Supports `Google` (Gemini realtime) and `OpenAI` (realtime). OpenAI can optionally use `google.beta.GeminiTTS` while keeping OpenAI for the LLM. Provider/model/voice are read from `agent_config.json` at startup.

**Tool Context Lifecycle**: `CustomerQueryTools` holds references to `client`, `session`, `ctx`, and optional `call_id`. Tools are async and use `asyncio.to_thread` for blocking REST calls. `end_call` triggers delayed room/session shutdown. `schedule_human_callback` updates the persisted call metadata when a call ID is present.

### Data Flow (Hermes-Initiated Outbound Example)

1. Hermes calls `make_phone_call` → plugin posts to `call_api.py` `/calls`.
2. `call_api.py` authenticates `CALL_API_TOKEN`, validates allowed country prefixes, creates the PostgreSQL call record, creates a LiveKit room, and dispatches the worker selected by `LIVEKIT_AGENT_NAME`.
3. `call_api.py` creates the outbound SIP participant using `OUTBOUND_TRUNK_ID` (`ST_...`) with `wait_until_answered=True`.
4. `call_api.py` records and returns the exact setup result (`answered` or a structured `failed_*` status with raw SIP code/text when available).
5. `agent.py` receives `outbound_dial_mode="api"`, resolves outbound call context, optionally looks up the phone in Frappe, waits for the API-created SIP participant to become active, and starts the realtime conversation worker linked to that participant.
6. After the callee answers and speaks first, the realtime agent responds. Hermes can call `get_phone_call_status(call_id)` or the operator can inspect `/dashboard`.
7. On disconnect, the worker posts a LiveKit session report; the API stores transcript fields and refreshes Vobiz recording metadata.

### Data Flow (Google Sheets GST/Document Campaign)

1. Dashboard `POST /agent/start` starts `_run_sheets_automation_wrapper()` in the Call API process.
2. `sheet_calling_automation.py` reads Sheet 1 rows with `Data Received Status=Pending`.
3. Rows with no prior log, or due `Next Action=AI Call`, trigger `POST /calls` with source metadata (`source=sheets_automation`, `cid=<CID>`).
4. A GST/document purpose causes `agent.py` to append campaign rules and prefer `schedule_human_callback`.
5. Completed/failed call records are synced to Sheet 2, including client comment, next action, date/time, CID, call datetime, recording proxy URL, and transcript.
6. Sheet 1 column 8 (`Last Comment`) and column 9 (`Count`) are updated.

## Configuration

**Environment variables** (see `.env.example`):

- LiveKit: `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`
- Worker selection: `LIVEKIT_AGENT_NAME` (recommended values: `outbound-caller-local`, `outbound-caller-dokploy`, `outbound-caller-prod`), optional `LIVEKIT_AGENT_HTTP_PORT`
- Frappe: `FRAPPE_SITE_URL`, `FRAPPE_API_KEY`, `FRAPPE_API_SECRET`
- AI: `GOOGLE_API_KEY` or `OPENAI_API_KEY`
- Telephony: `OUTBOUND_TRUNK_ID`, `VOBIZ_SIP_DOMAIN`, `DEFAULT_TRANSFER_NUMBER`
- Recording lookup: `VOBIZ_API_BASE_URL`, `VOBIZ_AUTH_ID`, `VOBIZ_AUTH_TOKEN`, optional recording format/channel settings
- Call-control API: `CALL_API_TOKEN`, `CALL_API_ALLOWED_COUNTRY_PREFIXES`, `CALL_API_DEFAULT_COUNTRY_CODE`, `CALL_API_MAX_PURPOSE_CHARS`, required `CALL_API_DATABASE_URL` / `CALL_STATUS_DATABASE_URL` for Postgres
- Hermes/plugin/client: `LIVEKIT_CALL_API_URL`, `LIVEKIT_CALL_API_TOKEN` (same value as `CALL_API_TOKEN` on the API service)
- Google Sheets automation: `GOOGLE_SHEETS_SPREADSHEET_ID`, `GOOGLE_SHEETS_CREDS_PATH`

`OUTBOUND_TRUNK_ID` must be the LiveKit SIP trunk ID from LiveKit Cloud → Telephony → SIP trunks (for example `ST_...`), **not** a phone number. Restart the API after changing `OUTBOUND_TRUNK_ID`, `LIVEKIT_AGENT_NAME`, or LiveKit credentials used by API-owned dialing. Restart the worker after changing worker-consumed `.env` values.

**agent_config.json** (committed):

- `agent_type`, `provider`, `model`, `voice`
- `noise_cancellation`, `custom_tts`
- `support_agent` / `sales_agent`: `name`, direction-specific greetings, `system_prompt`
- System prompts use `{lead_name}`, `{company_name}`, `{verification_rules}` placeholders

## Important Implementation Notes

- Greeting selection uses `_select_initial_greeting_template` and prefers direction-specific keys.
- For outbound, the first LLM response is not auto-generated; the agent waits for callee speech.
- `AgentSession` is configured with a short `user_away_timeout`; there is also explicit inactivity monitoring that shuts down after silence.
- DTMF handling for OTP bypasses the normal LLM path and directly feeds the 4-digit buffer.
- PDF sending resolves a print format (prefers "Sales Order with payment details" when a Payment Entry exists) and constructs a Frappe download URL.
- All tool responses are natural-language strings intended for voice; raw JSON/IDs are summarized.
- For local call-control testing, run both the worker and API with the same `.env` and `LIVEKIT_AGENT_NAME=outbound-caller-local`.
- With API-owned dialing, `POST /calls` intentionally blocks until answer/failure and returns the immediate SIP setup outcome. Keep client timeouts long enough for ringing/no-answer.
- The Call API owns final SIP failure status. Worker disconnect handlers should not overwrite `failed_*` records with `completed` after rejected/busy/no-answer legs.
- In local, Dokploy, LiveKit Cloud, or any separate-container deployment, set the same Postgres URL (`CALL_API_DATABASE_URL` or `CALL_STATUS_DATABASE_URL`) on both API and worker so status writes, `schedule_human_callback` metadata, dashboard data, and Google Sheets sync all use one shared store.
- Transcript/recording storage is implemented in the Call API status database: the worker posts LiveKit session history to `/internal/calls/{call_id}/session-report`, and recording URLs come from Vobiz callback/polling fields.
- `/calls/{call_id}` and `/dashboard/data` require bearer auth. `/calls/{call_id}/recording` is intentionally a server-side proxy route for inline dashboard playback.
- Runtime files `agent_running.flag`, `agent_stop.flag`, and `agent_error.log` are operational artifacts. They should be ignored and not committed.
- To stop local services, terminate terminals or run `pkill -f "uvicorn call_api:app"`. To stop only the sheet automation loop/active dashboard calls, use `POST /agent/kill` from the dashboard/API.
- SIP failure `object cannot be found` usually means `OUTBOUND_TRUNK_ID` is wrong. SIP `486 Busy Here` maps to `failed_busy`; `480 Temporarily Unavailable` maps best-effort to `failed_unreachable`; `408 Request Timeout` maps to `failed_no_answer`.
- Frappe lookup failures (for example local `127.0.0.1:8002` refused) fall back to an unknown lead and do not by themselves block outbound dialing.

The worker is deployed to LiveKit Cloud (which runs the Dockerfile with `uv run agent.py start` under the hood) using:

```bash
lk agent deploy --project "360ithub" --region ap-south --yes
```

The worker connects outbound to LiveKit.

The Call API is deployed as a separate service/process using:

```bash
uv run uvicorn call_api:app --host 0.0.0.0 --port 8000
```

Expose the Call API only behind HTTPS, bearer auth, and tight country-prefix restrictions. The Call API service must be configured with `LIVEKIT_AGENT_NAME=outbound-caller-prod` to dispatch the LiveKit Cloud worker.

Docs:

- `docs/hermes-call-control.md` — Call API/Hermes/dashboard/recording contract.
- `docs/google-sheets-calling-automation.md` — Sheet schema and campaign runbook.
- `docs/dokploy.md` — deployment metadata and MCP-first redeploy workflow.
