#!/bin/bash
# ponytail: one-command runner for cloud-worker sheet calling.

set -euo pipefail

cd "$(dirname "$0")"

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

VENV_BIN=".venv/bin"
SPREADSHEET_ID="${GOOGLE_SHEETS_SPREADSHEET_ID:-1_OXV6OAvrhgaSOnp03uJn8no8h3qTpk3g2lUX8CRnH4}"
API_HOST="${CALL_API_HOST:-127.0.0.1}"
API_PORT="${CALL_API_PORT:-8000}"
API_URL="${LIVEKIT_CALL_API_URL:-http://${API_HOST}:${API_PORT}}"

if [ ! -x "$VENV_BIN/uvicorn" ]; then
  echo "Error: .venv is missing uvicorn. Run: uv sync"
  exit 1
fi

if [ -z "${CALL_API_TOKEN:-}" ]; then
  echo "Error: CALL_API_TOKEN is missing in .env"
  exit 1
fi

if [ ! -f "${GOOGLE_SHEETS_CREDS_PATH:-.google_sheets_creds.json}" ]; then
  echo "Error: Google Sheets credentials file not found: ${GOOGLE_SHEETS_CREDS_PATH:-.google_sheets_creds.json}"
  exit 1
fi

export GOOGLE_SHEETS_SPREADSHEET_ID="$SPREADSHEET_ID"
export LIVEKIT_AGENT_NAME="${LIVEKIT_AGENT_NAME:-outbound-caller-prod}"

if curl -fsS "$API_URL/health" >/dev/null 2>&1; then
  echo "Call API already running at $API_URL"
else
  echo "Starting Call API at http://${API_HOST}:${API_PORT} using worker ${LIVEKIT_AGENT_NAME}"
  nohup "$VENV_BIN/uvicorn" call_api:app --host "$API_HOST" --port "$API_PORT" > call_api.log 2>&1 &

  for _ in $(seq 1 30); do
    if curl -fsS "http://${API_HOST}:${API_PORT}/health" >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done

  if ! curl -fsS "http://${API_HOST}:${API_PORT}/health" >/dev/null 2>&1; then
    echo "Error: Call API did not start. Check call_api.log"
    exit 1
  fi
fi

echo "Starting Google Sheets calling loop for spreadsheet $SPREADSHEET_ID"
curl -fsS -X POST "$API_URL/agent/start" \
  -H "Authorization: Bearer ${CALL_API_TOKEN}" \
  -H "Content-Type: application/json"
echo
echo "Dashboard: $API_URL/dashboard"
