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

## Development & Mock Testing

The local setup is used **exclusively for writing code, running unit tests, or testing conversation behaviors in the mock sandbox**. Production traffic and active PSTN calling are routed through LiveKit Cloud and Dokploy/VPS.

### Web UI Sandbox Tester
You can test the agent's prompts and conversation logic locally without placing real PSTN/telephony calls:

1. Run the local sandbox server:
   ```bash
   uv run web_ui_server.py
   ```
2. Open `http://localhost:8080` in your browser.
3. Select the **Mock Outbound** profile (this passes `outbound_dial_mode=mock` to the cloud worker, allowing you to converse with the agent via WebRTC in the browser without placing a phone call).

---

## Production Deployment & Operations

Production services are fully hosted:
- **Agent Worker (`agent.py`)** runs on **LiveKit Cloud**.
- **Call-Control API (`call_api.py`)** runs on **Dokploy/VPS** using `Dockerfile.api`.

For full deployment steps, refer to:
- [docs/dokploy.md](docs/dokploy.md) — Dokploy API deployment and container configurations.
- [AGENTS.md](AGENTS.md) — Deploying the worker to LiveKit Cloud.
- [docs/google-sheets-calling-automation.md](docs/google-sheets-calling-automation.md) — Running Google Sheets campaigns via the Dokploy API background loop.

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
