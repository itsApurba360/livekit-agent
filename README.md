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

For live deployment IDs, dashboard links, and redeploy commands, see [docs/dokploy.md](docs/dokploy.md).

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
