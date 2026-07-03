# Standalone REST-Decoupled LiveKit Agent

This repository contains an outbound-first LiveKit Voice AI Agent for Frappe/ERPNext. The worker runs as a pure Python LiveKit service and talks to Frappe/ERPNext only through HTTP REST APIs — no local Frappe bench imports or ERPNext package coupling.

Current deployable pieces:

- `agent.py` — LiveKit conversation worker.
- `call_api.py` — authenticated call-control API for Hermes/external automations, dashboard, recordings, and call status.
- `sheet_calling_automation.py` — Google Sheets driven outbound GST/document-collection campaign loop.
- `web_ui_server.py` — local browser sandbox for direct voice-agent testing.
- `integrations/hermes/livekit-caller/` — Hermes plugin for `make_phone_call` and call-status lookup.

---

## Architecture Overview

```text
Hermes / dashboard / Google Sheets automation
  → call_api.py (FastAPI, bearer auth, PostgreSQL call store)
      → LiveKit room dispatch + outbound SIP participant
          → agent.py worker (conversation only)
              → FrappeRestClient → remote Frappe/ERPNext REST API
              → WhatsApp OTP/PDF/text tools when allowed
      → Vobiz recording lookup/proxy
      → dashboard + Google Sheets call-log sync
```

The agent uses `FrappeRestClient` to:

1. Lookup callers against `Customer`, `Contact`, or `Lead` records.
2. Query invoices, sales orders, outstanding amounts, and customer profiles.
3. Call whitelisted `watoolx_whatsapp` endpoints for OTP verification and WhatsApp document delivery.

---

## Local Setup

### 1. Prerequisites

Install `uv` if needed:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. Configure environment

```bash
cp .env.example .env
```

Required groups in `.env`:

- LiveKit: `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `LIVEKIT_AGENT_NAME`
- Frappe: `FRAPPE_SITE_URL`, `FRAPPE_API_KEY`, `FRAPPE_API_SECRET`
- Model: `GOOGLE_API_KEY` or `OPENAI_API_KEY`
- Telephony: `OUTBOUND_TRUNK_ID` (`ST_...`, not a phone number), `VOBIZ_SIP_DOMAIN`, optional `DEFAULT_TRANSFER_NUMBER`
- Call API persistence: required `CALL_API_DATABASE_URL` / `CALL_STATUS_DATABASE_URL` for Postgres
- Hermes/client: `LIVEKIT_CALL_API_URL`, `LIVEKIT_CALL_API_TOKEN`
- Optional Google Sheets campaign automation: `GOOGLE_SHEETS_SPREADSHEET_ID`, `GOOGLE_SHEETS_CREDS_PATH`
- Optional Vobiz recording lookup: `VOBIZ_API_BASE_URL`, `VOBIZ_AUTH_ID`, `VOBIZ_AUTH_TOKEN`

Keep `.env` and Google service-account JSON files out of git.

### 3. Install dependencies

```bash
uv sync
```

The project now includes Google Sheets dependencies (`gspread`, `google-auth`) for `sheet_calling_automation.py`.

---

## Running Locally

> [!NOTE]
> The Agent Worker (`agent.py`) is deployed and hosted on **LiveKit Cloud** (under agent ID `CA_ct6s7UGyzoju` with dispatch name `outbound-caller-prod`). **No local worker process needs to be run locally going forward.** All environments (local API, staging, production) dispatch to this cloud worker.

### Call-control API

Run the call control API locally. It is configured to dispatch the worker on LiveKit Cloud:

```bash
set -a && source .env && set +a
LIVEKIT_AGENT_NAME=outbound-caller-prod uv run uvicorn call_api:app --host 127.0.0.1 --port 8000
```

Verify:

```bash
curl -s http://127.0.0.1:8000/health
```

Open the dashboard:

```bash
open http://127.0.0.1:8000/dashboard
```

The dashboard asks for `CALL_API_TOKEN`, loads protected data from `GET /dashboard/data`, shows inline recording playback when available, and exposes Start Agent / Kill Switch controls for the Google Sheets automation loop. For deployments where API and worker are separate containers/hosts, configure both with the same Postgres URL so dashboard, worker status updates, callbacks, and Sheets sync share one status store.

### Trigger one call through the API

```bash
curl -X POST "http://127.0.0.1:8000/calls" \
  -H "Authorization: Bearer <CALL_API_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "phone_number": "+91XXXXXXXXXX",
    "purpose": "Local integration test call from Hermes setup",
    "agent_type": "sales",
    "requested_by": "manual-local-test"
  }'
```

`POST /calls` blocks until the immediate setup/dial result is known and returns statuses such as `answered`, `failed_busy`, `failed_no_answer`, `failed_unreachable`, `failed_rejected`, `failed_trunk`, or `failed`.

### Google Sheets campaign automation

See [docs/google-sheets-calling-automation.md](docs/google-sheets-calling-automation.md).

Quick local one-cycle run:

```bash
set -a && source .env && set +a
uv run sheet_calling_automation.py
```

Dashboard-managed loop:

- **Start Agent** → `POST /agent/start`
- **Kill Switch** → `POST /agent/kill` and terminate active LiveKit rooms known to the status store

Runtime flag/log files (`agent_running.flag`, `agent_stop.flag`, `agent_error.log`) are operational artifacts and should not be committed.

### Web UI sandbox tester

```bash
uv run web_ui_server.py
```

Open [http://localhost:8080](http://localhost:8080), select a profile, connect, and speak. The worker must already be running.

---

## Tests

Targeted unit tests use `unittest` and stub LiveKit heavily; these do not require external services:

```bash
.venv/bin/python -m unittest discover -s tests -p test_agent_tools.py -v
.venv/bin/python -m unittest discover -s tests -p test_agent_call_context.py -v
.venv/bin/python -m unittest discover -s tests -p test_web_ui.py -v
.venv/bin/python -m unittest discover -s tests -p test_call_outcomes.py -v
.venv/bin/python -m unittest discover -s tests -p test_call_api.py -v
.venv/bin/python -m unittest discover -s tests -p test_hermes_livekit_plugin.py -v
.venv/bin/python -m unittest discover -s tests -p test_sheet_automation.py -v
```

Full discovery can include integration-style Frappe tests under `tests/`; run it only when the configured Frappe site is reachable:

```bash
.venv/bin/python -m unittest discover -s tests
```

Root-level and Frappe integration tests require real services and a configured `.env`:

```bash
.venv/bin/python -m unittest test_remote_agent.py
.venv/bin/python -m unittest discover -s tests -p test_frappe_connection.py -v
```

---

## Documentation Map

- [AGENTS.md](AGENTS.md) — working notes for AI/coding agents in this repo.
- [docs/hermes-call-control.md](docs/hermes-call-control.md) — Hermes plugin, Call API, dashboard, recording/transcript, and endpoint contract.
- [docs/google-sheets-calling-automation.md](docs/google-sheets-calling-automation.md) — Google Sheets campaign loop and sheet schema.
- [docs/dokploy.md](docs/dokploy.md) — non-secret Dokploy metadata and deployment workflow.

---

## Deployment

### Worker

The Dockerfile installs via `uv` and runs:

```bash
uv run agent.py start
```

The worker connects outbound to LiveKit and does not need an inbound public port.

### Call-control API

Deploy the API as a separate service/process:

```bash
uv run uvicorn call_api:app --host 0.0.0.0 --port 8000
```

Expose it only behind HTTPS, bearer auth, and tight country-prefix restrictions. The API service needs LiveKit credentials because it creates rooms, dispatches the worker, and creates SIP participants. Hermes should receive only `LIVEKIT_CALL_API_URL` and `LIVEKIT_CALL_API_TOKEN`, never LiveKit API secrets.

For Dokploy details, see [docs/dokploy.md](docs/dokploy.md). For Hermes plugin wiring, see [docs/hermes-call-control.md](docs/hermes-call-control.md).

### Deploying the Cloud Worker

To deploy code updates to the LiveKit Cloud hosted worker, run:

```bash
lk agent deploy --project "project-360ithub-live" --region ap-south --yes
```

To update environment variables/secrets on the cloud agent without doing a code rebuild:

```bash
lk agent update-secrets --id CA_PUFV6Djq5we3 --project "project-360ithub-live" --secrets-file ".env" --yes
```
