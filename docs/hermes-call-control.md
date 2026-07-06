# Hermes Call Control

This guide explains how **Hermes Agent**, the dashboard, and Google Sheets automation place outbound calls through the LiveKit call-control API (`call_api.py`) while the LiveKit worker (`agent.py`) stays portable across local, Dokploy, and LiveKit Cloud deployments.

## Architecture

```text
Hermes make_phone_call tool / dashboard / sheet_calling_automation.py
  → POST /calls (Bearer CALL_API_TOKEN / LIVEKIT_CALL_API_TOKEN)
      → call_api.py
          → validate token, phone number, and purpose
          → create/update PostgreSQL call record
          → create LiveKit room
          → dispatch selected worker by LIVEKIT_AGENT_NAME
          → create SIP participant with wait_until_answered=True
          → persist and return exact dial status
      → agent.py worker
          → receives outbound_dial_mode=api metadata
          → waits for the API-created SIP participant
          → handles the answered conversation
          → posts LiveKit session report back to the API
      → Vobiz lookup/proxy
          → stores recording metadata and serves inline audio through /calls/{id}/recording
```

Hermes never receives LiveKit server credentials. It only needs:

- `LIVEKIT_CALL_API_URL` — base URL of the call API, e.g. `https://api.yourdomain.com` (Dokploy/VPS URL).
- `LIVEKIT_CALL_API_TOKEN` — same secret value as `CALL_API_TOKEN` on the API service.

The Call API persists call records through `call_status_store.py`. Set `CALL_API_DATABASE_URL` (preferred) or `CALL_STATUS_DATABASE_URL` to a Postgres connection string on both the API and worker. There is no local file-backed fallback; missing Postgres configuration is a startup/runtime configuration error.

## Worker dispatch names

Use the production LiveKit Cloud worker dispatch name:

- `outbound-caller-prod` — production LiveKit Cloud worker

## Setup and environment configuration

### 1. livekit_agent configuration (Dokploy app secrets)

```bash
CALL_API_TOKEN=<openssl rand -hex 32>
CALL_API_ALLOWED_COUNTRY_PREFIXES=+91
CALL_API_DEFAULT_COUNTRY_CODE=+91
OUTBOUND_TRUNK_ID=ST_xxxxxxxxxxxxxxxxx
LIVEKIT_AGENT_NAME=outbound-caller-prod
LIVEKIT_CALL_API_URL=https://api.yourdomain.com
LIVEKIT_CALL_API_TOKEN=<same as CALL_API_TOKEN>
# Shared Postgres database between API and worker:
CALL_API_DATABASE_URL=postgresql://user:password@host:5432/database
```

Optional Google Sheets campaign automation:

```bash
GOOGLE_SHEETS_SPREADSHEET_ID=<spreadsheet-id>
GOOGLE_SHEETS_CREDS_PATH=.google_sheets_creds.json
```

Optional Vobiz recording lookup:

```bash
VOBIZ_API_BASE_URL=https://api.vobiz.ai/api/v1
VOBIZ_AUTH_ID=<auth-id>
VOBIZ_AUTH_TOKEN=<auth-token>
```

### 2. Hermes environment

In Hermes config (`~/.hermes/config.yaml` under `environment:`) or the shell that starts Hermes:

```yaml
environment:
  LIVEKIT_CALL_API_URL: "https://api.yourdomain.com"
  LIVEKIT_CALL_API_TOKEN: "<same as CALL_API_TOKEN>"
```

### 3. Enable the Hermes plugin

Hermes discovers user plugins from `~/.hermes/plugins/<name>/`. Copy or symlink this repo's plugin there, then enable it:

```bash
mkdir -p ~/.hermes/plugins
cp -R integrations/hermes/livekit-caller ~/.hermes/plugins/livekit-caller
hermes plugins enable livekit-caller
```

Restart Hermes after changing plugin/config values.

### 4. Running the Call API

The Call API runs on Dokploy/VPS using `Dockerfile.api`. To verify that it's healthy, query the hosted health endpoint:

```bash
curl -s https://api.yourdomain.com/health
```

### 5. Use from Hermes

Ask Hermes to call a number with a clear purpose. Hermes should use the `make_phone_call` tool with `phone_number` and `purpose`. Optional fields are `agent_type` (`sales` or `support`), `customer_name`, and `company_name`.

`POST /calls` returns the immediate setup/dial result. `get_phone_call_status(call_id)` remains useful for persisted status, recording/transcript metadata, and event history.

## Status examples

Immediate `/calls` statuses include:

- `answered` — API created the SIP participant and LiveKit reported answer.
- `failed_busy` — carrier/SIP returned busy, commonly `486 Busy Here`.
- `failed_no_answer` — timeout/no answer, commonly `408 Request Timeout`.
- `failed_unreachable` — unavailable/unreachable, commonly `480 Temporarily Unavailable`.
- `failed_rejected` — callee/carrier rejected the call, commonly `603 Decline`.
- `failed_trunk` — SIP trunk/auth/provider failure.
- `failed` — other API-owned dial error.

Persisted records can also show transitional or post-answer statuses such as `dispatching`, `dispatched`, `dialing`, `answered`, `active`, and `completed`.

## Dashboard

Open:

```bash
open https://api.yourdomain.com/dashboard
```

Paste `CALL_API_TOKEN`. The static page does not embed call data; browser requests send `Authorization: Bearer <token>` to protected JSON endpoints.

The dashboard currently shows:

- summary cards and recent calls from `GET /dashboard/data`;
- per-call detail + event timeline from `GET /calls/{call_id}`;
- inline `<audio controls>` playback via `GET /calls/{call_id}/recording` when Vobiz recording metadata exists;
- a **Fetch** button for `POST /calls/{call_id}/refresh-recording`;
- a **Start Agent** button for the Google Sheets automation loop (`POST /agent/start`);
- a **Kill Switch** (`POST /agent/kill`) that stops the loop and deletes active LiveKit rooms tracked in PostgreSQL.

Recording playback intentionally uses the Call API proxy, not direct Vobiz URLs. The proxy adds server-side Vobiz auth and supports byte ranges for seeking/scrubbing.

## API contract

| Endpoint | Auth | Purpose |
| --- | --- | --- |
| `GET /health` | none | Process health. |
| `POST /calls` | Bearer | Create room, dispatch worker, dial SIP participant, persist/return setup outcome. |
| `GET /calls/{call_id}` | Bearer | Persisted call record plus event history. |
| `POST /calls/{call_id}/kill` | Bearer | Delete one active LiveKit room and mark the call killed. |
| `POST /calls/{call_id}/refresh-recording` | Bearer or `?token=` | Poll Vobiz and update recording metadata. |
| `GET /calls/{call_id}/recording` | none | Server-side Vobiz recording proxy for dashboard inline audio. |
| `GET /dashboard` | none | Static browser UI shell. |
| `GET /dashboard/data` | Bearer | Summary, recent calls, agent running flag, and last agent error. |
| `GET /agent/status` | Bearer | Google Sheets automation running state and active calls. |
| `POST /agent/start` | Bearer | Start Sheets automation loop inside the Call API process. |
| `POST /agent/kill` | Bearer | Write stop flag, remove running flag, delete active rooms, mark active calls killed. |
| `POST /internal/calls/{call_id}/session-report` | internal bearer or `?token=` | Worker posts LiveKit session report/transcript; handler also triggers Vobiz lookup. |
| `POST /internal/vobiz/recording-callback` | internal bearer or `?token=` | Store Vobiz recording callback metadata. |

Successful answered setup returns `ok`, `call_id`, `room_name`, `status`, `phone_number`, and often `sip_call_id`. Structured failures return `ok: false`, a `failed_*` status, `reason`, raw SIP status fields when available, and `error` when available.

## Transcript and recording metadata

The Call API stores LiveKit transcript/session-report payloads and Vobiz recording metadata in PostgreSQL. The source split is:

- **Transcript / session report:** LiveKit. The worker posts session history/report payloads to the internal Call API endpoint after outbound disconnect.
- **Call recording URL:** Vobiz API. Do not use LiveKit egress as the primary source for operator-facing recordings unless this decision changes.

Current internal endpoint:

```text
POST /internal/calls/{call_id}/session-report
Authorization: Bearer <CALL_API_INTERNAL_TOKEN or CALL_API_TOKEN>
```

Accepted payload fields include:

```json
{
  "room_name": "agent_call_call_xxx",
  "transcript_source": "livekit",
  "report": {"items": []},
  "session_report": {"items": []},
  "transcript_text": "assistant: ...\nuser: ..."
}
```

The handler stores `transcript_source`, `transcript_text`, and `session_report_json`, then calls the Vobiz lookup path to update `vobiz_call_uuid`, `vobiz_recording_id`, `recording_source`, `recording_url`, duration, format, and type when available.

Large transcript/recording payloads should eventually move to object storage; PostgreSQL currently keeps metadata, transcript text, JSON report, and URLs/pointers.

## Google Sheets campaign automation

The Call API exposes dashboard controls for `sheet_calling_automation.py`. That script reads pending rows from Google Sheets, posts outbound calls to `/calls`, syncs completed calls back to Sheet 2, and updates Sheet 1 counters/comments. See [google-sheets-calling-automation.md](./google-sheets-calling-automation.md) for the sheet schema and runbook.

Relevant runtime files:

- `agent_running.flag`
- `agent_stop.flag`
- `agent_error.log`

They are generated operational files and should not be committed.

## Deployment matrix

### Dokploy API dispatching a LiveKit Cloud worker

Set the Dokploy API app environment to dispatch the production worker and own SIP dialing:

```env
LIVEKIT_AGENT_NAME=outbound-caller-prod
CALL_API_TOKEN=<long random token>
OUTBOUND_TRUNK_ID=ST_xxxxxxxxxxxxxxxxx
CALL_API_ALLOWED_COUNTRY_PREFIXES=+91
```

The Dokploy Call API still needs LiveKit URL/API credentials because it creates rooms, dispatches workers, and creates SIP participants. Hermes should only receive the call API URL/token.

### LiveKit Cloud worker

Set worker secrets for Frappe/model access, the same worker name, and the internal Call API callback URL/token if you want session reports to return to the API:

```text
LIVEKIT_AGENT_NAME: outbound-caller-prod
FRAPPE_SITE_URL: <site url>
FRAPPE_API_KEY: <api key>
FRAPPE_API_SECRET: <api secret>
OPENAI_API_KEY: <optional>
GOOGLE_API_KEY: <optional>
CALL_API_INTERNAL_URL or LIVEKIT_CALL_API_URL: <https call-api base url>
CALL_API_INTERNAL_TOKEN or LIVEKIT_CALL_API_TOKEN: <internal/API token>
```

LiveKit Cloud injects LiveKit connection credentials for the worker deployment; do not manually set those as LiveKit Cloud worker secrets.

Important: set the same `CALL_API_DATABASE_URL` / `CALL_STATUS_DATABASE_URL` on the LiveKit Cloud worker and the hosted Call API. `agent.py` posts LiveKit session reports back to the API over HTTP when the callback URL/token are configured, while post-answer status events and `schedule_human_callback` metadata write through `call_status_store.py`; Postgres makes those writes visible to the API/dashboard/Sheets from every host.

## Safety

- Keep `CALL_API_ALLOWED_COUNTRY_PREFIXES` tight (default `+91`).
- Use a long random `CALL_API_TOKEN`.
- Keep Vobiz and Google service-account credentials on the Call API side only.
- Do not expose the API publicly without HTTPS, bearer auth, and rate limits.
- Confirm with the user before placing real calls or starting a sheet-driven campaign.
- Do not run multiple sheet automation loops against the same sheet without an external lock.


## Production (Dokploy)

See [dokploy.md](./dokploy.md) to deploy the API application. Point Hermes `LIVEKIT_CALL_API_URL` at the HTTPS URL of that API app.
