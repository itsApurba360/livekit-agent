# Standalone REST-Decoupled LiveKit Agent

This repository contains the standalone LiveKit Voice AI Agent configured to handle inbound and outbound calls. It is completely decoupled from the Frappe bench local codebase, running as a pure Python service that communicates with any target Frappe/ERPNext instance via the standard Frappe HTTP REST API.

---

## Architecture Overview

```
 ┌───────────────────────┐                  ┌───────────────────────┐
 │                       │  REST API Calls  │                       │
 │  LiveKit Agent Worker ├─────────────────>│ Remote Frappe/ERPNext │
 │     (This Project)    │                  │       Instance        │
 │                       │<─────────────────┤                       │
 └───────────┬───────────┘                  └───────────────────────┘
             │
             │ WebSockets
             v
 ┌───────────────────────┐
 │                       │
 │    LiveKit Server     │
 │                       │
 └───────────────────────┘
```

The agent uses a standalone `FrappeRestClient` to:

1. Lookup callers matching telephone numbers in `Customer`, `Contact`, or `Lead` tables.
2. Query sales invoice details, sales orders, pending outstanding amounts, and customer profiles.
3. Call remote whitelisted endpoints of the `watoolx_whatsapp` app (if installed) to send OTP verification codes and document PDFs.

---

## Local Setup & Running

### 1. Prerequisites

Ensure you have the `uv` package manager installed. If not, install it using:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. Configure Environment Variables

Copy `.env.example` to `.env` and fill in the required credentials:

```bash
cp .env.example .env
```

Key settings in `.env`:

- `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`: Credentials to connect to your LiveKit Server.
- `FRAPPE_SITE_URL`: The domain of the target remote Frappe/ERPNext instance.
- `FRAPPE_API_KEY`, `FRAPPE_API_SECRET`: Token keys generated on the remote Frappe site for authentication.
- `GOOGLE_API_KEY` or `OPENAI_API_KEY`: API key for model processing.

### 3. Install Dependencies

Run `uv sync` to set up the virtual environment:

```bash
uv sync
```

_(Note: If you run into `onnxruntime` resolution warnings on macOS Intel during local sync, you can alternatively initialize with `uv pip install livekit livekit-agents requests pytest python-dotenv` to execute code and test logic without media plugins)._

### 4. Local Configurations

Customize the prompts, greeting templates, models, and voice settings inside `agent_config.json`.

### 5. Running the Test Suite

Run unit tests to verify the REST API communications and validation constraints:

```bash
.venv/bin/python -m unittest test_remote_agent.py
```

### 6. Starting the Worker Locally

Run the agent worker in startup mode:

```bash
uv run agent.py start
or
.venv/bin/python agent.py start
```

### 7. Starting the Web UI Tester (Sandbox)

Run the web-based sandbox interface locally to test the voice agent directly in your browser:

```bash
uv run web_ui_server.py
or
.venv/bin/python web_ui_server.py
```

Open [http://localhost:8080](http://localhost:8080) in your browser. Choose either **Customer** (for Support Agent Kavya) or **Sales Lead** (for Sales Agent Nandini), click "Connect", grant microphone access, and start speaking to test prompt logic and tool calls in real time.

---

## Docploy Deployment

This project includes a Dockerfile configured for deployment on containerized platforms like **Docploy**.

For live deployment IDs, dashboard links, and redeploy commands, see [docs/dokploy.md](docs/dokploy.md). AI agents should use the `dokploy-mcp` MCP server when present (see that doc).

### Local Call Control Test for Hermes / External AI

Before deploying a public API, verify the call-control flow locally with two local processes.

1. Start the LiveKit worker with a local-only dispatch name:

```bash
LIVEKIT_AGENT_NAME=outbound-caller-local uv run agent.py start
```

2. In another terminal, start the call-control API using the same `.env` LiveKit credentials and dispatch name. The API owns room creation, worker dispatch, SIP dialing, and the immediate answered/failed status result:

```bash
CALL_API_TOKEN=local-test-token \
CALL_API_ALLOWED_COUNTRY_PREFIXES=+91 \
CALL_API_DEFAULT_COUNTRY_CODE=+91 \
LIVEKIT_AGENT_NAME=outbound-caller-local \
uv run uvicorn call_api:app --host 127.0.0.1 --port 8000
```

3. Health-check the API:

```bash
curl -s http://127.0.0.1:8000/health
```

Expected response:

```json
{"ok":true}
```

4. After the worker and API are both running, trigger exactly one approved local test call:

```bash
curl -X POST "http://127.0.0.1:8000/calls" \
  -H "Authorization: Bearer $CALL_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "phone_number": "+919****1141",
    "purpose": "Local integration test call from Hermes setup",
    "agent_type": "sales",
    "requested_by": "manual-local-test"
  }'
```

`POST /calls` returns the immediate dial outcome, such as `answered`, `failed_busy`, `failed_no_answer`, `failed_unreachable`, `failed_rejected`, or `failed_trunk`. See [docs/hermes-call-control.md](docs/hermes-call-control.md) for Hermes plugin wiring and the local/Dokploy/LiveKit Cloud worker-name matrix.

### Deployment Steps on Docploy:

1. **Create a New Application**: Select Dockerfile deployment in Docploy.
2. **Repository**: Link this folder/repository to the Docploy application.
3. **Environment Variables**: Populate the following variables in the Docploy app settings:
   - `LIVEKIT_URL`
   - `LIVEKIT_API_KEY`
   - `LIVEKIT_API_SECRET`
   - `FRAPPE_SITE_URL`
   - `FRAPPE_API_KEY`
   - `FRAPPE_API_SECRET`
   - `GOOGLE_API_KEY` or `OPENAI_API_KEY`
   - `OUTBOUND_TRUNK_ID` (for dialing out)
   - `VOBIZ_SIP_DOMAIN`
   - `DEFAULT_TRANSFER_NUMBER`
4. **Port Allocation**: Since LiveKit agent workers run as background workers connecting _outbound_ to the LiveKit Server via WebSockets, **you do not need to expose any ports** or configure inbound routing rules on Docploy.
5. **Deploy**: Trigger the deploy pipeline. The Dockerfile will automatically build, install all dependencies via `uv`, and launch the worker using:
   ```bash
   CMD ["/root/.local/bin/uv", "run", "agent.py", "start"]
   ```
