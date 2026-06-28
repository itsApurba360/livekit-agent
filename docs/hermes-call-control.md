# Hermes Call Control

This guide explains how to let **Hermes Agent** place outbound calls through the LiveKit voice worker using the local or deployed **call-control API** (`call_api.py`).

## Architecture

```
Hermes (make_phone_call tool)
    → POST /calls (Bearer LIVEKIT_CALL_API_TOKEN)
        → call_api.py
            → LiveKit room + agent dispatch
                → agent.py (outbound SIP)
```

Hermes never receives LiveKit server credentials. It only needs:

- `LIVEKIT_CALL_API_URL` — base URL of the call API (e.g. `http://127.0.0.1:8000` locally)
- `LIVEKIT_CALL_API_TOKEN` — same value as `CALL_API_TOKEN` on the API service

The call API persists call records in a local SQLite database. By default this is
`call_control.sqlite3` in the repo root. Override it with `CALL_API_DB_PATH` when
the API and worker should share a specific mounted path.

## Local setup

### 1. Environment

In the **livekit_agent** repo `.env`:

```bash
CALL_API_TOKEN=<openssl rand -hex 32>
CALL_API_ALLOWED_COUNTRY_PREFIXES=+91
CALL_API_DEFAULT_COUNTRY_CODE=+91
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

The resulting config should include:

```yaml
plugins:
  enabled:
    - livekit-caller
```

Restart Hermes after changing config.

### 3. Run worker + API

Terminal A:

```bash
cd /path/to/livekit_agent
uv run agent.py start
```

Terminal B:

```bash
cd /path/to/livekit_agent
set -a && source .env && set +a
uv run uvicorn call_api:app --host 127.0.0.1 --port 8000
```

Health check:

```bash
curl -s http://127.0.0.1:8000/health
```

### 4. Use from Hermes

Ask Hermes to call a number with a clear purpose, for example:

> Call +919062371141 and follow up on our ERPNext implementation enquiry.

Hermes should use the `make_phone_call` tool with `phone_number` and `purpose`. Optional: `agent_type` (`sales` | `support`), `customer_name`, `company_name`.
It returns a `call_id`. Ask Hermes for call status with that `call_id`; the
`get_phone_call_status` tool reads `GET /calls/{call_id}` and reports the latest
stored status.

Status examples:

- `dispatching` / `dispatched` — request accepted by the call API / LiveKit dispatch
- `dialing` — the worker has started outbound SIP dialing
- `answered` / `active` — LiveKit SIP reports the call was answered and the SIP participant is in the room
- `completed` — the SIP participant disconnected after an answered call
- `failed_busy` — carrier/SIP returned busy, commonly `486 Busy Here`
- `failed_unreachable` — carrier/SIP reported unavailable/unreachable, commonly `480 Temporarily Unavailable`
- `failed_no_answer` — timeout/no answer, commonly `408 Request Timeout`
- `failed_rejected` — callee/carrier rejected the call, commonly `603 Decline`
- `failed_trunk` — SIP trunk/auth/provider failure

## Local dashboard

Open the built-in dashboard in a browser:

```bash
open http://127.0.0.1:8000/dashboard
```

Paste `CALL_API_TOKEN` into the dashboard. The HTML page itself does not embed
call data; it fetches `GET /dashboard/data` with an `Authorization: Bearer ...`
header from the browser. The dashboard shows summary cards, recent calls, SIP
status, and a clickable per-call event timeline.

## API contract (summary)

| Endpoint | Auth | Body |
|----------|------|------|
| `GET /health` | none | — |
| `POST /calls` | `Authorization: Bearer <CALL_API_TOKEN>` | `phone_number`, `purpose`, optional fields |
| `GET /calls/{call_id}` | Bearer | — |
| `GET /dashboard` | none | Browser UI; data still requires token |
| `GET /dashboard/data` | Bearer | Optional `limit` query param |

Successful dispatch returns `call_id`, `room_name`, `status`, `phone_number`.
`GET /calls/{call_id}` returns the persisted call record, status reason, raw SIP
status fields when available, timestamps, metadata, and status event history.

## Safety

- Keep `CALL_API_ALLOWED_COUNTRY_PREFIXES` tight (default `+91`).
- Use a long random `CALL_API_TOKEN`.
- Do not expose the API on the public internet without HTTPS and rate limits.
- Confirm with the user before placing real calls.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -p test_call_api.py -v
.venv/bin/python -m unittest discover -s tests -p test_hermes_livekit_plugin.py -v
```

## Production (Dokploy)

See [dokploy.md](./dokploy.md) — verify locally first, then deploy a **separate** API application with `uv run uvicorn call_api:app --host 0.0.0.0 --port 8000`. Point Hermes `LIVEKIT_CALL_API_URL` at the HTTPS URL of that app.