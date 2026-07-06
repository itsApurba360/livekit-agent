#!/bin/bash
# ponytail: simple runner script using virtual environment instead of uv

set -e

# Ensure we are in the script's directory
cd "$(dirname "$0")"

# Source environment variables if .env exists
if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

VENV_BIN=".venv/bin"

if [ ! -d "$VENV_BIN" ]; then
  echo "Error: Virtual environment (.venv) not found. Please create it first."
  exit 1
fi

CMD=${1:-api}

case "$CMD" in
  api)
    [ $# -gt 0 ] && shift
    echo "Starting call-control API on port 8000..."
    exec "$VENV_BIN/uvicorn" call_api:app --host 127.0.0.1 --port 8000 "$@"
    ;;
  web)
    [ $# -gt 0 ] && shift
    echo "Starting Web UI sandbox server..."
    exec "$VENV_BIN/python" web_ui_server.py "$@"
    ;;
  *)
    echo "Usage: $0 {api|web} [args...]"
    exit 1
    ;;
esac
