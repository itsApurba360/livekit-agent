# Hermes Call Control

This guide explains how to let **Hermes Agent** place outbound calls through the LiveKit call-control API (`call_api.py`) while the LiveKit worker (`agent.py`) stays portable across local, Dokploy, and LiveKit Cloud deployments.

## Architecture

```text
Hermes (make_phone_call tool)
  → POST /calls (Bearer LIVEKIT_CALL_API_TOKEN)
      → call_api.py
          → validate token, phone number, and purpose
          → create LiveKit room
          → dispatch selected worker by LIVEKIT_AGENT_NAME
          → create SIP participant with wait_until_answered=True
          → persist and return exact dial status
      → agent.py worker
          → receives outbound_dial_mode=api metadata
          → waits for the API-created SIP participant
          → handles the answered conversation
```

Hermes never receives LiveKit server credentials. It only needs:

- `LIVEKIT_CALL_API_URL` — base URL of the call API (for example `http://127.0.0.1:8000` locally)
- `LIVEKIT_CALL_API_TOKEN` — same value as `CALL_API_TOKEN` on the API service

The call API persists call records in a local SQLite database. By default this is `call_control.sqlite3` in the repo root. Override it with `CALL_API_DB_PATH` when the API needs a specific mounted path.

## Worker dispatch names

Use distinct worker names so LiveKit dispatch does not route a job to the wrong environment:

- `outbound-caller-local` — local testing
- `outbound-caller-dokploy` — Dokploy testing/staging
- `outbound-caller-prod` — production LiveKit Cloud worker

Do not run two workers with the same `LIVEKIT_AGENT_NAME` in the same LiveKit project.

## Local setup

### 1. Environment

In the **livekit_agent** repo `.env`:

```bash
CALL_API_TOKEN=<openssl rand -hex 32>
CALL_API_ALLOWED_COUNTRY_PREFIXES=+91
CALL_API_DEFAULT_COUNTRY_CODE=+91
OUTBOUND_TRUNK_ID=ST_xxxxxxxxxxxxxxxxx
LIVEKIT_AGENT_NAME=outbound-caller-local
```

In **Hermes** (`~/.hermes/config.yaml` under `environment:` or your shell profile):

```yaml
environment:
  LIVEKIT_CALL_API_URL: "http://127.0.0.1:8000"
  LIVEKIT_CALL_API_TOKEN: "<same as CALL_API_TOKEN>"
```

### 2. Enable the plugin

Hermes discovers user plugins from `~/.hermes/plugins/<name>/`. Copy or symlink this repo's plugin there, then enable it:

```bash
mkdir -p ~/.hermes/plugins
cp -R integrations/hermes/livekit-caller ~/.hermes/plugins/livekit-caller
hermes plugins enable livekit-caller
```

Restart Hermes after changing config.

### 3. Run worker + API

Terminal A:

```bash
cd /path/to/livekit_agent
set -a && source .env && set +a
LIVEKIT_AGENT_NAME=outbound-caller-local uv run agent.py start
```

Terminal B:

```bash
cd /path/to/livekit_agent
set -a && source .env && set +a
LIVEKIT_AGENT_NAME=outbound-caller-local uv run uvicorn call_api:app --host 127.0.0.1 --port 8000
```

Health check:

```bash
curl -s http://127.0.0.1:8000/health
```

### 4. Use from Hermes

Ask Hermes to call a number with a clear purpose. Hermes should use the `make_phone_call` tool with `phone_number` and `purpose`. Optional fields are `agent_type` (`sales` or `support`), `customer_name`, and `company_name`.

`POST /calls` now returns the immediate setup/dial result. `get_phone_call_status` remains useful for dashboard/SQLite lookup by `call_id`.

## Status examples

Immediate `/calls` statuses include:

- `answered` — API created the SIP participant and LiveKit reported answer
- `failed_busy` — carrier/SIP returned busy, commonly `486 Busy Here`
- `failed_no_answer` — timeout/no answer, commonly `408 Request Timeout`
- `failed_unreachable` — unavailable/unreachable, commonly `480 Temporarily Unavailable`
- `failed_rejected` — callee/carrier rejected the call, commonly `603 Decline`
- `failed_trunk` — SIP trunk/auth/provider failure
- `failed` — other API-owned dial error

Persisted records can also show transitional or post-answer statuses such as `dispatching`, `dispatched`, `active`, and `completed`.

## Local dashboard

Open the built-in dashboard in a browser:

```bash
open http://127.0.0.1:8000/dashboard
```

Paste `CALL_API_TOKEN` into the dashboard. The HTML page itself does not embed call data; it fetches `GET /dashboard/data` with an `Authorization: Bearer <token>` header from the browser. The dashboard shows summary cards, recent calls, SIP status, and a clickable per-call event timeline.

## API contract (summary)

| Endpoint | Auth | Body |
|----------|------|------|
| `GET /health` | none | — |
| `POST /calls` | Bearer | `phone_number`, `purpose`, optional fields |
| `GET /calls/{call_id}` | Bearer | — |
| `GET /dashboard` | none | Browser UI; data still requires token |
| `GET /dashboard/data` | Bearer | Optional `limit` query param |

Successful answered setup returns `ok`, `call_id`, `room_name`, `status`, `phone_number`, and often `sip_call_id`. Structured failures return `ok: false`, a `failed_*` status, `reason`, raw SIP status fields when available, and `error` when available. `GET /calls/{call_id}` returns the persisted call record, timestamps, metadata, and status event history.

## Deployment matrix

### Local API + local worker

```bash
LIVEKIT_AGENT_NAME=outbound-caller-local uv run agent.py start
LIVEKIT_AGENT_NAME=outbound-caller-local uv run uvicorn call_api:app --host 127.0.0.1 --port 8000
```

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

Set worker secrets for Frappe and model access plus the same worker name:

```text
LIVEKIT_AGENT_NAME: outbound-caller-prod
FRAPPE_SITE_URL: <site url>
FRAPPE_API_KEY: <api key>
FRAPPE_API_SECRET: <api secret>
OPENAI_API_KEY: <optional>
GOOGLE_API_KEY: <optional>
```

LiveKit Cloud injects LiveKit connection credentials for the worker deployment; do not manually set those as LiveKit Cloud worker secrets.

## Transcript and recording metadata

The Call API now stores LiveKit transcript/session-report payloads and Vobiz recording metadata in the call status database. The source split is:

- **Transcript / session report:** LiveKit. The worker should collect the LiveKit Agents session report/history at call end and POST it back to the Call API.
- **Call recording URL:** Vobiz API. Do not use LiveKit egress as the primary source for the operator-facing recording URL unless this decision changes.

The worker posts to this internal endpoint after the callee disconnects:

```text
POST /internal/calls/{call_id}/session-report
Authorization: Bearer <CALL_API_INTERNAL_TOKEN>
```

Planned payload shape:

```json
{
  "room_name": "agent_call_call_xxx",
  "transcript_source": "livekit",
  "report": "ctx.make_session_report().to_dict()",
  "recording_source": "vobiz",
  "recording_url": "https://..."
}
```

Recording/transcript payloads should be stored outside SQLite when they grow large. SQLite keeps metadata, status, source fields (`livekit` / `vobiz`), transcript text, session report JSON, and report/recording pointers.

The Vobiz recording lookup should be implemented behind the Call API using non-committed environment variables for the Vobiz recording endpoint and credentials. Hermes should continue to call only the authenticated Call API and should not receive Vobiz credentials directly.

Vobiz docs requirements for recording URLs:

- Base URL: `https://api.vobiz.ai/api/v1`
- Auth headers: `X-Auth-ID`, `X-Auth-Token`, and `Content-Type: application/json`
- Active-call recording start endpoint: `POST /Account/{auth_id}/Call/{call_uuid}/Record/`
- Recording list endpoint: `GET /Account/{auth_id}/Recording/?call_uuid={call_uuid}`
- Single recording endpoint: `GET /Account/{auth_id}/Recording/{recording_id}/`
- Recording callback field to store when available: `record_url`
- Recommended callback URL shape: `/internal/vobiz/recording-callback?token={internal_token}&call_id={call_id}`
- Recording object field to store when polling/listing: `recording_url`

The implementation therefore needs the Vobiz `call_uuid` for the PSTN leg. If Vobiz trunk webhooks are not configured to give us that UUID, the Call API should correlate after call end by searching Vobiz CDRs for the outbound number/time window and then cross-reference `CDR.uuid` with `Recording.call_uuid`.

## Safety

- Keep `CALL_API_ALLOWED_COUNTRY_PREFIXES` tight (default `+91`).
- Use a long random `CALL_API_TOKEN`.
- Do not expose the API publicly without HTTPS, bearer auth, and rate limits.
- Confirm with the user before placing real calls.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -p test_call_outcomes.py -v
.venv/bin/python -m unittest discover -s tests -p test_call_api.py -v
.venv/bin/python -m unittest discover -s tests -p test_agent_call_context.py -v
.venv/bin/python -m unittest discover -s tests -p test_hermes_livekit_plugin.py -v
```

## Production (Dokploy)

See [dokploy.md](./dokploy.md). Verify locally first, then deploy a **separate** API application with `uv run uvicorn call_api:app --host 0.0.0.0 --port 8000`. Point Hermes `LIVEKIT_CALL_API_URL` at the HTTPS URL of that app.
