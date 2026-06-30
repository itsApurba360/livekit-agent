# Dokploy Deployment Reference

This file records non-secret deployment metadata so future sessions (human or AI) can find and redeploy the LiveKit agent without rediscovering IDs from scratch.

**Do not commit API keys, `.env` values, Google service-account JSON, or Vobiz credentials here.**

## For AI agents — use MCP first

**If the `dokploy-mcp` MCP server is available in this workspace, use it for all Dokploy operations.** Do not ask the user for the API key or fall back to raw `curl` unless MCP is missing or failing.

MCP is configured locally in `.cursor/mcp.json` (gitignored). After opening this project in Cursor, check whether `dokploy-mcp` tools are listed in the session. If they are, prefer MCP.

### MCP-first workflow

1. **Discover** — `project-all` or `application-one` with `applicationId: vBCXzDVrjNT175I1RZgHV`.
2. **Deploy** — `application-deploy` with `applicationId: vBCXzDVrjNT175I1RZgHV`. Call **once** per release.
3. **Check status** — `application-one` or `deployment-all`.
4. **Containers** — `docker-getContainersByAppNameMatch` with `appName: livekit-agent`.
5. **Cleanup** — `docker-removeContainer` for exited containers; `settings-cleanStoppedContainers` / `settings-cleanUnusedImages` if needed.
6. **Env vars** — `application-saveEnvironment` when updating secrets. Read values from local `.env`; never commit them.

### MCP setup (if missing)

```json
{
  "mcpServers": {
    "dokploy-mcp": {
      "command": "npx",
      "args": ["-y", "@dokploy/mcp"],
      "env": {
        "DOKPLOY_URL": "http://173.212.216.156:3000",
        "DOKPLOY_API_KEY": "<your-api-key>"
      }
    }
  }
}
```

Reload Cursor after adding. Optional: limit tools with `DOKPLOY_ENABLED_TAGS=project,application,deployment,docker,settings`.

When MCP is not present, use the non-secret IDs in this file and ask the user for `DOKPLOY_URL` / `DOKPLOY_API_KEY` only if needed.

## Server

| Setting | Value |
| --- | --- |
| Dokploy URL | `http://173.212.216.156:3000` |
| API auth header | `x-api-key: <DOKPLOY_API_KEY>` |

## Project & Application

| Resource | Name | ID |
| --- | --- | --- |
| Project | `agents` | `LWsYBVpSGZGAEuc5Yss9b` |
| Environment | `production` (default) | `hmAdxEdvBX1o4mveDrJFu` |
| Application | `livekit-agent` | `vBCXzDVrjNT175I1RZgHV` |
| Docker app name | `livekit-agent-ur38zy` | — |

**Dashboard:** http://173.212.216.156:3000/dashboard/project/LWsYBVpSGZGAEuc5Yss9b/environment/hmAdxEdvBX1o4mveDrJFu

## Source & Build

| Setting | Value |
| --- | --- |
| Git repo | https://github.com/itsApurba360/livekit-agent |
| Branch | `master` |
| Build type | `dockerfile` |
| Dockerfile | `Dockerfile` (context: `.`) |
| Auto-deploy | enabled (on git push) |
| Worker inbound ports | none (worker connects outbound to LiveKit) |

## Current architecture relevance

The worker is deployed to **LiveKit Cloud** (agent ID: `CA_ct6s7UGyzoju`) under name `outbound-caller-prod`. No local or Dokploy-based worker runs going forward.

The Call API app runs separately (e.g., on Dokploy or locally) to handle Hermes, dashboard, recordings, Google Sheets automation, or external HTTP call control:

- Call API app: runs `uv run uvicorn call_api:app --host 0.0.0.0 --port 8000`; expose only behind HTTPS and bearer auth.
- The API app needs LiveKit URL/API credentials because it creates rooms, dispatches workers, and creates SIP participants, and must be configured with `LIVEKIT_AGENT_NAME=outbound-caller-prod` to target the Cloud worker.

## Local verification before deploy

Verify Call API locally:

```bash
# Call API
set -a && source .env && set +a
LIVEKIT_AGENT_NAME=outbound-caller-prod uv run uvicorn call_api:app --host 127.0.0.1 --port 8000
```

```bash
curl -s http://127.0.0.1:8000/health
open http://127.0.0.1:8000/dashboard
```

For Google Sheets campaigns, also verify `GOOGLE_SHEETS_SPREADSHEET_ID`, `GOOGLE_SHEETS_CREDS_PATH`, and the sheet schema in `docs/google-sheets-calling-automation.md` before using the dashboard Start Agent button.

## Environment Variables

Set in Dokploy application settings. Values live in local `.env`, not in git.

### Worker app

- `LIVEKIT_URL`
- `LIVEKIT_API_KEY`
- `LIVEKIT_API_SECRET`
- `LIVEKIT_AGENT_NAME` (usually `outbound-caller-dokploy` for Dokploy worker testing)
- `FRAPPE_SITE_URL`
- `FRAPPE_API_KEY`
- `FRAPPE_API_SECRET`
- `GOOGLE_API_KEY` and/or `OPENAI_API_KEY`
- `OUTBOUND_TRUNK_ID` (legacy/manual worker-dial fallback only; API-owned dialing is preferred)
- `VOBIZ_SIP_DOMAIN` (optional)
- `DEFAULT_TRANSFER_NUMBER` (optional)

For a LiveKit Cloud worker, configure worker-side Frappe/model secrets plus `LIVEKIT_AGENT_NAME=outbound-caller-prod`; LiveKit Cloud injects its own LiveKit connection credentials. If transcript/session-report posting should work, also configure `CALL_API_INTERNAL_URL` (or `LIVEKIT_CALL_API_URL`) and `CALL_API_INTERNAL_TOKEN` (or `LIVEKIT_CALL_API_TOKEN`) pointing at the hosted Call API.

### Call API app

- `LIVEKIT_URL`
- `LIVEKIT_API_KEY`
- `LIVEKIT_API_SECRET`
- `LIVEKIT_AGENT_NAME` (set to the worker it dispatches, e.g. `outbound-caller-prod`)
- `OUTBOUND_TRUNK_ID` (`ST_...`, not a phone number)
- `CALL_API_TOKEN`
- `CALL_API_ALLOWED_COUNTRY_PREFIXES` (optional, default `+91`)
- `CALL_API_DEFAULT_COUNTRY_CODE` (optional, default `+91`)
- `CALL_API_MAX_PURPOSE_CHARS` (optional)
- `CALL_API_DATABASE_URL` / `CALL_STATUS_DATABASE_URL` for required Postgres persistence. Use the Dokploy Postgres app `agents-postgress-m6nqpj` connection string here.
- Recording lookup settings: `VOBIZ_API_BASE_URL`, `VOBIZ_AUTH_ID`, `VOBIZ_AUTH_TOKEN`, optional `VOBIZ_RECORDING_FORMAT` / `VOBIZ_RECORDING_CHANNEL_TYPE`
- Google Sheets campaign settings if the dashboard Start Agent loop runs on Dokploy: `GOOGLE_SHEETS_SPREADSHEET_ID`, `GOOGLE_SHEETS_CREDS_PATH`

The Call API app should use Postgres for dashboard/call history when the worker is on LiveKit Cloud or a different Dokploy app. Runtime files still remain local to the API process: `agent_running.flag`, `agent_stop.flag`, and `agent_error.log`; they are operational artifacts, not source files.

## Agent Config (runtime)

Voice/model settings are in `agent_config.json` (committed). Current OpenAI setup:

- Provider: `OpenAI`
- Model: `gpt-realtime-mini`
- Voice: `marin` (OpenAI female realtime voice; do not use Google voices like `zephyr` for OpenAI realtime)

Config changes in `agent_config.json` require a rebuild/redeploy.

## Deploy Workflow

> **AI agents:** Use MCP tools above when `dokploy-mcp` is available. The steps below are the human / no-MCP fallback.

### Standard code/config change

```bash
git add .
git commit -m "your message"
git push origin master
```

Auto-deploy should pick up the push. If not, trigger one manual deploy via MCP/UI/API.

### Manual deploy via API

```bash
curl -X POST \
  -H "x-api-key: <DOKPLOY_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"applicationId":"vBCXzDVrjNT175I1RZgHV","title":"your deploy title"}' \
  "http://173.212.216.156:3000/api/application.deploy"
```

**Important:** Call `application.deploy` only once per release. Calling it twice queues duplicate builds.

### Check status

```bash
curl -G \
  -H "x-api-key: <DOKPLOY_API_KEY>" \
  --data-urlencode "applicationId=vBCXzDVrjNT175I1RZgHV" \
  "http://173.212.216.156:3000/api/application.one"
```

### List running containers

```bash
curl -G \
  -H "x-api-key: <DOKPLOY_API_KEY>" \
  --data-urlencode "appName=livekit-agent" \
  "http://173.212.216.156:3000/api/docker.getContainersByAppNameMatch"
```

## Cleanup After Redeploys

> **AI agents:** Prefer `docker-removeContainer`, `settings-cleanStoppedContainers`, and `settings-cleanUnusedImages` via MCP when available.

Docker Swarm leaves stopped containers after redeploys. To clean up:

```bash
# Remove a specific exited container
curl -X POST \
  -H "x-api-key: <DOKPLOY_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"containerId":"<id>"}' \
  "http://173.212.216.156:3000/api/docker.removeContainer"

# Or use Dokploy settings endpoints
curl -X POST \
  -H "x-api-key: <DOKPLOY_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{}' \
  "http://173.212.216.156:3000/api/settings.cleanStoppedContainers"

curl -X POST \
  -H "x-api-key: <DOKPLOY_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{}' \
  "http://173.212.216.156:3000/api/settings.cleanUnusedImages"
```

## Notes

- `FRAPPE_SITE_URL` must be reachable from the Dokploy server; private LAN IPs like `192.168.x.x` will not work unless networked.
- The worker app needs no inbound public route; the Call API app does.
- The Call API dashboard and Google Sheets loop operate from the API process. If multiple API replicas are used, move state/locking out of local flag files first.
- If the worker runs in a different container/host from the Call API, use the same Postgres connection string from Dokploy app `agents-postgress-m6nqpj` on both API and worker for full dashboard/Sheets parity.
- See `README.md`, `docs/hermes-call-control.md`, and `docs/google-sheets-calling-automation.md` for local runbooks and API contracts.
