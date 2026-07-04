# AGENTS.md

This file provides guidance to AI coding agents working in this repository.

## Project Overview

Standalone LiveKit outbound call-control codebase. The current use of this repository is managing outbound PSTN calls, their status, recordings, dashboard views, and Google Sheets campaign automation.

Current scope is outbound-call management only:

- **Call API (`call_api.py`)** owns Hermes/external requests, room creation, worker dispatch, outbound SIP dialing, status persistence, dashboard data, recording proxy/lookup, and Google Sheets automation controls.
- **Worker (`agent.py`)** owns live conversation after the API-created PSTN participant answers.
- **Google Sheets automation (`sheet_calling_automation.py`)** can run GST/document-collection campaigns by reading pending sheet rows, posting `/calls`, and syncing completed outcomes back to the sheet.

Inbound calls and Frappe/ERPNext integration are not the focus right now. Treat inbound paths and Frappe-backed customer lookup/WhatsApp/PDF tools as legacy or optional support surfaces; do not expand or prioritize them unless the user explicitly asks.

Two personas remain in config, but new work should only touch them when outbound campaign behavior requires it:

- **Support (Kavya)**: Legacy existing-customer persona. Can query orders, invoices, balances, and in GST/document campaigns schedules human callbacks instead of collecting documents directly.
- **Sales (Nandini)**: For leads/unknown callers. Qualifies requirements.

## Development Commands

### Environment Setup

```bash
uv sync
cp .env.example .env
# Edit .env with LIVEKIT_*, CALL_API_*, and GOOGLE_API_KEY or OPENAI_API_KEY.
# FRAPPE_* is only needed when explicitly testing legacy Frappe-backed tools.
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

For local worker testing, use a distinct dispatch name so the local process does not compete with production:

```bash
set -a && source .env && set +a
LIVEKIT_AGENT_NAME=outbound-caller-local .venv/bin/python agent.py dev
```

For local runs in this repo, prefer the existing virtualenv Python (`.venv/bin/python`) instead of `uv run`. Some local platforms fail resolving `onnxruntime==1.27.0` during `uv run`, while the checked-out virtualenv already has the working dependency set.

Hermes, the dashboard, and sheet automation trigger outbound PSTN calls through `call_api.py`. Run the Call API:

```bash
set -a && source .env && set +a
LIVEKIT_AGENT_NAME=outbound-caller-prod .venv/bin/python -m uvicorn call_api:app --host 127.0.0.1 --port 8000
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

Use this when the campaign is driven from the Excel-like Google Sheet (`Master Sheet`) rather than by manually posting one `/calls` request.

The sheet is the source of truth for who to call:

- Sheet 1 / `Master Sheet`: client queue. Rows with `Data Received Status=Pending`, `AI Enabled=Yes`, and a dialable `Mobile Number` are eligible.
- Sheet 2 / `Followups`: call log and next-action history. Completed calls are synced here with outcome, transcript, recording link, notes, and the next action.

Preferred one-command runner for the hosted LiveKit Cloud worker:

```bash
./run_sheet_calls.sh
```

This loads `.env`, defaults `GOOGLE_SHEETS_SPREADSHEET_ID` to `1_OXV6OAvrhgaSOnp03uJn8no8h3qTpk3g2lUX8CRnH4` when unset, starts `call_api.py` only if the API is not already healthy, sets `LIVEKIT_AGENT_NAME=outbound-caller-prod` by default, and posts `POST /agent/start`. It does **not** start a local `agent.py` worker.

Operational flow from the sheet:

1. Confirm `.env` has `GOOGLE_SHEETS_SPREADSHEET_ID`, `GOOGLE_SHEETS_CREDS_PATH`, `CALL_API_TOKEN`, LiveKit credentials, `OUTBOUND_TRUNK_ID`, and the shared Postgres URL.
2. Run `./run_sheet_calls.sh`.
3. The Call API stays local on `127.0.0.1:8000`, dispatches `outbound-caller-prod` on LiveKit Cloud, and dials through the configured SIP trunk.
4. The loop scans pending `Master Sheet` rows, posts each eligible row to `/calls`, and avoids duplicates using active call status plus `CID`.
5. After calls finish, sync writes the call outcome, transcript, recording proxy URL, comments, next action, and attempt counts back to `Followups` and `Master Sheet`.

One cycle:

```bash
set -a && source .env && set +a
.venv/bin/python sheet_calling_automation.py
```

Dashboard-managed loop:

- `POST /agent/start` starts the loop inside the Call API process and repeats roughly every 15 seconds.
- `POST /agent/kill` writes `agent_stop.flag`, removes `agent_running.flag`, deletes active LiveKit rooms tracked in PostgreSQL, and marks active calls killed.
- `POST /calls/{call_id}/kill` terminates one call room.

See `docs/google-sheets-calling-automation.md` for sheet schema and operational rules.

### Running the Web UI Sandbox Tester

```bash
set -a && source .env && set +a
LIVEKIT_AGENT_NAME=outbound-caller-local .venv/bin/python web_ui_server.py
# Open http://localhost:8080 in browser, select a profile, connect, speak to test.
```

The web UI connects to the LiveKit Cloud project and dispatches whichever worker name is configured in `LIVEKIT_AGENT_NAME`. Use `outbound-caller-local` when testing a local worker.

Use the **Mock Outbound** profile to test outbound conversation behavior without placing a PSTN call. It sends `call_direction=outbound` and `outbound_dial_mode=mock`, so the worker keeps outbound prompting and waits for the user to speak first, but skips SIP participant creation.

### Running Tests

Targeted unit tests (no external services required):

```bash
.venv/bin/python -m unittest discover -s tests -p test_agent_call_context.py -v
.venv/bin/python -m unittest discover -s tests -p test_web_ui.py -v
.venv/bin/python -m unittest discover -s tests -p test_call_outcomes.py -v
.venv/bin/python -m unittest discover -s tests -p test_call_api.py -v
.venv/bin/python -m unittest discover -s tests -p test_hermes_livekit_plugin.py -v
.venv/bin/python -m unittest discover -s tests -p test_sheet_automation.py -v
```

Full discovery should stay focused on outbound call management:

```bash
.venv/bin/python -m unittest discover -s tests
```

Tests use Python's `unittest` framework despite `pytest` in dev dependencies. Many tests heavily stub LiveKit modules.

## Architecture

### Core Modules and Responsibilities

| File | Role |
|------|------|
| `agent.py` | LiveKit conversation worker entrypoint, call context detection, API-dial participant wait path, legacy worker-dial fallback, session setup, dynamic prompt compilation, GST/document campaign rules, silence timeout handling, DTMF listener for OTP |
| `agent_tools.py` | `CustomerQueryTools` (extends `llm.ToolContext`): currently exposes only outbound campaign tools (`schedule_human_callback`, `schedule_ai_followup`, `end_call`); legacy customer lookup/WhatsApp/PDF methods remain disabled from model use |
| `frappe_client.py` | Legacy optional `FrappeRestClient`: pure REST client using Token auth. Methods: `lookup_caller`, `get_resource`, `get_resource_list`, `send_whatsapp_message*` |
| `agent_config.json` | Runtime config: provider/model/voice, agent personas (prompts + direction-specific greetings), noise_cancellation, custom_tts |
| `web_ui_server.py` | Minimal HTTP server serving static web tester; mints LiveKit tokens and dispatches the agent into test rooms |
| `run_sheet_calls.sh` | One-command local launcher for Google Sheets campaigns using the hosted LiveKit Cloud worker; starts the Call API if needed and triggers `/agent/start` |
| `call_api.py` | FastAPI call-control service: validates bearer auth, normalizes/limits phone numbers, persists call records, serves dashboard/API data, creates LiveKit rooms, dispatches workers, creates outbound SIP participants, maps dial outcomes, proxies recordings, accepts session reports, and starts/kills sheet automation |
| `call_outcomes.py` | Shared SIP outcome mapping used by the API and worker (`486` → `busy`, `408` → `no_answer`, etc.) |
| `call_status_store.py` | PostgreSQL persistence layer for outbound call records, event history, transcript/session-report fields, Vobiz recording fields, and metadata. Requires `CALL_API_DATABASE_URL` or `CALL_STATUS_DATABASE_URL`. |
| `call_dashboard.py` | Self-contained HTML/CSS/JS dashboard rendered by `GET /dashboard`; data loads from authenticated JSON endpoints; inline recording playback uses `/calls/{call_id}/recording` |
| `sheet_calling_automation.py` | Google Sheets campaign loop: reads pending rows, calls `/calls`, prevents duplicate active dials, syncs completed call outcomes/transcripts/recording links to Sheet 2, updates Sheet 1 comment/count |
| `vobiz_client.py` | Vobiz REST client for recording metadata lookup and recording media proxy streaming/range support |
| `integrations/hermes/livekit-caller/` | Hermes plugin exposing `make_phone_call` and `get_phone_call_status`; install under `~/.hermes/plugins/livekit-caller` and enable with `hermes plugins enable livekit-caller` |

### Key Architectural Patterns

**Frappe Boundary (legacy/optional)**: Frappe-backed customer lookup, WhatsApp, and PDF helpers are not current focus areas. If explicitly touched, keep all Frappe interaction behind `FrappeRestClient`; do not add local `frappe` or `erpnext` imports or bench coupling.

**Call Context and Direction**: Outbound metadata is the source of truth for current work. `CallContext` still has legacy inbound detection through SIP participant attributes and room name patterns, but new work should target outbound `agent_call_*` flows unless explicitly requested otherwise.

**Outbound Campaign Prompting**: For outbound purposes containing `gst`, `document`, `filing`, or `pdf`, `agent.py` appends GST/document campaign rules that route the conversation toward a human callback instead of OTP/document collection.

**Outbound SIP Dialing and Status Tracking**: API-owned dialing is the default. `call_api.py` creates the LiveKit room, dispatches the selected worker, creates the SIP participant with `wait_until_answered=True`, maps exact SIP outcomes via `call_outcomes.py`, stores status in PostgreSQL, and returns the immediate setup result. The worker receives `outbound_dial_mode="api"`, waits for the API-created SIP participant to become active, starts the `AgentSession` linked to that participant, and does not place a second SIP call. `_ensure_outbound_participant` remains as a legacy/manual fallback for worker-owned dialing.

**Hermes Call Control**: `call_api.py` is the boundary for Hermes and other external AIs. It requires `Authorization: Bearer <CALL_API_TOKEN>`, accepts `POST /calls`, creates a room named `agent_call_<call_id>`, embeds outbound metadata, dispatches the worker, dials the PSTN leg, persists status in PostgreSQL, and returns the immediate setup/dial result (`answered`, `failed_busy`, `failed_no_answer`, `failed_unreachable`, `failed_rejected`, `failed_trunk`, or `failed`). Hermes only needs `LIVEKIT_CALL_API_URL` and `LIVEKIT_CALL_API_TOKEN`; it must not receive `LIVEKIT_API_SECRET`.

**Call Status Dashboard**: `GET /dashboard` serves a browser UI with summary cards, recent calls, SIP status, per-call timelines, inline recording playback, recording refresh, Start Agent, Kill Switch, and per-call kill controls. The UI asks for `CALL_API_TOKEN` and fetches protected data from JSON endpoints. Do not expose the API publicly without HTTPS and the same bearer-auth protections as `/calls`.

**Transcript and Recording Sources**: Transcript/session-report data comes from LiveKit. The worker posts session history to `/internal/calls/{call_id}/session-report`; the handler stores transcript/report data and triggers Vobiz recording lookup. Operator-facing recording URLs come from Vobiz, and playback goes through `GET /calls/{call_id}/recording` so the browser never needs Vobiz credentials. Hermes should only receive Call API URL/token values.

**Google Sheets Campaign Automation**: `sheet_calling_automation.py` reads Sheet 1 pending rows, posts `/calls` with `requested_by="sheets_automation"`, stores `cid/source` in call metadata, syncs completed calls back to Sheet 2, and marks metadata `synced=True`. `schedule_human_callback` writes `next_action`, callback date/time, and client notes into PostgreSQL metadata for the sync step.

**Hermes Plugin Lifecycle**: Hermes discovers user plugins from `~/.hermes/plugins/<name>/`, not arbitrary repo-relative paths unless project plugins are explicitly enabled. Copy or symlink `integrations/hermes/livekit-caller` to `~/.hermes/plugins/livekit-caller`, run `hermes plugins enable livekit-caller`, configure `LIVEKIT_CALL_API_URL` / `LIVEKIT_CALL_API_TOKEN`, then restart Hermes or start a new session before expecting `make_phone_call` and `get_phone_call_status` to appear.

**Provider Flexibility**: Supports `Google` (Gemini realtime) and `OpenAI` (realtime). OpenAI can optionally use `google.beta.GeminiTTS` while keeping OpenAI for the LLM. Provider/model/voice are read from `agent_config.json` at startup.

**Tool Context Lifecycle**: `CustomerQueryTools` holds references to `client`, `session`, `ctx`, and optional `call_id`. The model currently receives only `schedule_human_callback`, `schedule_ai_followup`, and `end_call`. `end_call` triggers delayed room/session shutdown; scheduling tools update persisted call metadata when a call ID is present.

### Data Flow (Hermes-Initiated Outbound Example)

1. Hermes calls `make_phone_call` → plugin posts to `call_api.py` `/calls`.
2. `call_api.py` authenticates `CALL_API_TOKEN`, validates allowed country prefixes, creates the PostgreSQL call record, creates a LiveKit room, and dispatches the worker selected by `LIVEKIT_AGENT_NAME`.
3. `call_api.py` creates the outbound SIP participant using `OUTBOUND_TRUNK_ID` (`ST_...`) with `wait_until_answered=True`.
4. `call_api.py` records and returns the exact setup result (`answered` or a structured `failed_*` status with raw SIP code/text when available).
5. `agent.py` receives `outbound_dial_mode="api"`, resolves outbound call context, optionally uses legacy Frappe lookup when configured, waits for the API-created SIP participant to become active, and starts the realtime conversation worker linked to that participant.
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
- Legacy Frappe tools, only when explicitly needed: `FRAPPE_SITE_URL`, `FRAPPE_API_KEY`, `FRAPPE_API_SECRET`
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
- System prompts use `{lead_name}` and `{company_name}` placeholders; `{verification_rules}` is legacy WhatsApp/Frappe prompt support.

## Important Implementation Notes

- Greeting selection uses `_select_initial_greeting_template` and prefers direction-specific keys.
- For outbound, the first LLM response is not auto-generated; the agent waits for callee speech.
- `AgentSession` is configured with a short `user_away_timeout`; there is also explicit inactivity monitoring that shuts down after silence.
- DTMF/OTP, WhatsApp, PDF, and customer-query tools are legacy Frappe surfaces and are not exposed to the model right now.
- All tool responses are natural-language strings intended for voice; raw JSON/IDs are summarized.
- For local call-control testing, run both the worker and API with the same `.env` and `LIVEKIT_AGENT_NAME=outbound-caller-local`.
- For local browser-only outbound testing, use the web UI **Mock Outbound** profile. This is intentionally not a telephony test: `outbound_dial_mode=mock` skips SIP dialing and only exercises local worker dispatch, metadata parsing, prompts, tools, and realtime audio.
- With API-owned dialing, `POST /calls` intentionally blocks until answer/failure and returns the immediate SIP setup outcome. Keep client timeouts long enough for ringing/no-answer.
- The Call API owns final SIP failure status. Worker disconnect handlers should not overwrite `failed_*` records with `completed` after rejected/busy/no-answer legs.
- In local, Dokploy, LiveKit Cloud, or any separate-container deployment, set the same Postgres URL (`CALL_API_DATABASE_URL` or `CALL_STATUS_DATABASE_URL`) on both API and worker so status writes, `schedule_human_callback` metadata, dashboard data, and Google Sheets sync all use one shared store.
- Transcript/recording storage is implemented in the Call API status database: the worker posts LiveKit session history to `/internal/calls/{call_id}/session-report`, and recording URLs come from Vobiz callback/polling fields.
- `/calls/{call_id}` and `/dashboard/data` require bearer auth. `/calls/{call_id}/recording` is intentionally a server-side proxy route for inline dashboard playback.
- Runtime files `agent_running.flag`, `agent_stop.flag`, and `agent_error.log` are operational artifacts. They should be ignored and not committed.
- To stop local services, terminate terminals or run `pkill -f "uvicorn call_api:app"`. To stop only the sheet automation loop/active dashboard calls, use `POST /agent/kill` from the dashboard/API.
- SIP failure `object cannot be found` usually means `OUTBOUND_TRUNK_ID` is wrong. SIP `486 Busy Here` maps to `failed_busy`; `480 Temporarily Unavailable` maps best-effort to `failed_unreachable`; `408 Request Timeout` maps to `failed_no_answer`.
- Frappe lookup failures (for example local `127.0.0.1:8002` refused) fall back to an unknown lead and must not block outbound dialing.

The worker is deployed to LiveKit Cloud (which runs the Dockerfile with `uv run agent.py start` under the hood) using:

```bash
lk agent deploy --project "project-360ithub-live" --region ap-south --yes
```

To update environment variables/secrets on the cloud agent without doing a code rebuild (avoiding running Docker locally):

```bash
lk agent update-secrets --id CA_PUFV6Djq5we3 --project "project-360ithub-live" --secrets-file ".env" --yes
```

The worker connects outbound to LiveKit.

The Call API is deployed as a separate service/process using:

```bash
uv run uvicorn call_api:app --host 0.0.0.0 --port 8000
```

Expose the Call API only behind HTTPS, bearer auth, and tight country-prefix restrictions. The Call API service must be configured with `LIVEKIT_AGENT_NAME=outbound-caller-prod` to dispatch the LiveKit Cloud worker.

## Working with Gemini 3.1 Live

When using `gemini-3.1-flash-live-preview` (which has mutable context = False):
- **No dynamic turn generation**: `session.generate_reply()` is not supported and will throw `RealtimeError`. Do not call it.
- **No text say without TTS**: `session.say(text)` requires a separate TTS. Calling it without an attached TTS model raises `RuntimeError` and silent crashes in background tasks.
- **No mid-session config updates**: Changing system prompt/instructions, chat context, or tools during the call will be ignored by the LiveKit Google realtime plugin. All context must be fully populated at session startup.
- **Outbound call initiation**: Avoid all python-side active greeting triggers (like silence monitors or startup `say`). Instead, instruct the model in its `system_prompt` with the initial greeting templates, and let it greet the user as soon as the user makes the first utterance.

Docs:

- `docs/hermes-call-control.md` — Call API/Hermes/dashboard/recording contract.
- `docs/google-sheets-calling-automation.md` — Sheet schema and campaign runbook.
- `docs/dokploy.md` — deployment metadata and MCP-first redeploy workflow.
