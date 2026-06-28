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

## API contract (summary)

| Endpoint | Auth | Body |
|----------|------|------|
| `GET /health` | none | — |
| `POST /calls` | `Authorization: Bearer $CALL_API_TOKEN` | `phone_number`, `purpose`, optional fields |
| `GET /calls/{call_id}` | Bearer | — |

Successful dispatch returns `call_id`, `room_name`, `status`, `phone_number`.

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