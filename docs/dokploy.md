# Dokploy Deployment Reference

This file records non-secret deployment metadata so future sessions (human or AI) can find and redeploy the LiveKit agent without rediscovering IDs from scratch.

**Do not commit API keys or `.env` values here.**

## Server

| Setting | Value |
|---------|-------|
| Dokploy URL | `http://173.212.216.156:3000` |
| API auth header | `x-api-key: <DOKPLOY_API_KEY>` |

## Project & Application

| Resource | Name | ID |
|----------|------|-----|
| Project | `agents` | `LWsYBVpSGZGAEuc5Yss9b` |
| Environment | `production` (default) | `hmAdxEdvBX1o4mveDrJFu` |
| Application | `livekit-agent` | `vBCXzDVrjNT175I1RZgHV` |
| Docker app name | `livekit-agent-ur38zy` | — |

**Dashboard:** http://173.212.216.156:3000/dashboard/project/LWsYBVpSGZGAEuc5Yss9b/environment/hmAdxEdvBX1o4mveDrJFu

## Source & Build

| Setting | Value |
|---------|-------|
| Git repo | https://github.com/itsApurba360/livekit-agent |
| Branch | `master` |
| Build type | `dockerfile` |
| Dockerfile | `Dockerfile` (context: `.`) |
| Auto-deploy | enabled (on git push) |
| Inbound ports | none (worker connects outbound to LiveKit) |

## Environment Variables

Set in Dokploy application settings (values live in local `.env`, not in git):

- `LIVEKIT_URL`
- `LIVEKIT_API_KEY`
- `LIVEKIT_API_SECRET`
- `FRAPPE_SITE_URL`
- `FRAPPE_API_KEY`
- `FRAPPE_API_SECRET`
- `GOOGLE_API_KEY`
- `OPENAI_API_KEY`
- `OUTBOUND_TRUNK_ID` (optional)
- `VOBIZ_SIP_DOMAIN` (optional)
- `DEFAULT_TRANSFER_NUMBER` (optional)

## Agent Config (runtime)

Voice/model settings are in `agent_config.json` (committed). Current OpenAI setup:

- Provider: `OpenAI`
- Model: `gpt-realtime-mini`
- Voice: `marin` (OpenAI female realtime voice; do not use Google voices like `zephyr`)

## Deploy Workflow

### Standard (code/config change)

```bash
git add .
git commit -m "your message"
git push origin master
```

Auto-deploy should pick up the push. If not, trigger one manual deploy via API or the Dokploy UI.

### Manual deploy via API

```bash
curl -X POST \
  -H "x-api-key: $DOKPLOY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"applicationId":"vBCXzDVrjNT175I1RZgHV","title":"your deploy title"}' \
  "http://173.212.216.156:3000/api/application.deploy"
```

**Important:** Call `application.deploy` only once per release. Calling it twice queues duplicate builds.

### Check status

```bash
curl -G \
  -H "x-api-key: $DOKPLOY_API_KEY" \
  --data-urlencode "applicationId=vBCXzDVrjNT175I1RZgHV" \
  "http://173.212.216.156:3000/api/application.one"
```

### List running containers

```bash
curl -G \
  -H "x-api-key: $DOKPLOY_API_KEY" \
  --data-urlencode "appName=livekit-agent" \
  "http://173.212.216.156:3000/api/docker.getContainersByAppNameMatch"
```

## Cursor / MCP Setup

Local file: `.cursor/mcp.json` (gitignored — contains secrets)

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

With MCP enabled, an agent can discover projects/apps via API tools instead of reading this file.

## Cleanup After Redeploys

Docker Swarm leaves stopped containers after redeploys. To clean up:

```bash
# Remove a specific exited container
curl -X POST -H "x-api-key: $DOKPLOY_API_KEY" -H "Content-Type: application/json" \
  -d '{"containerId":"<id>"}' \
  "http://173.212.216.156:3000/api/docker.removeContainer"

# Or use Dokploy settings endpoints
curl -X POST -H "x-api-key: $DOKPLOY_API_KEY" -H "Content-Type: application/json" \
  -d '{}' "http://173.212.216.156:3000/api/settings.cleanStoppedContainers"

curl -X POST -H "x-api-key: $DOKPLOY_API_KEY" -H "Content-Type: application/json" \
  -d '{}' "http://173.212.216.156:3000/api/settings.cleanUnusedImages"
```

## Notes

- `FRAPPE_SITE_URL` must be reachable from the Dokploy server (private LAN IPs like `192.168.x.x` will not work unless networked).
- Config changes in `agent_config.json` require a redeploy (rebuild) to take effect in the container.
- See also `README.md` → Docploy Deployment section for the original setup guide.