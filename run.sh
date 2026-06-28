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

CMD=${1:-agent}

case "$CMD" in
  agent)
    [ $# -gt 0 ] && shift
    echo "Starting agent worker..."
    exec "$VENV_BIN/python" agent.py start "$@"
    ;;
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
  test)
    [ $# -gt 0 ] && shift
    echo "Running tests..."
    exec "$VENV_BIN/python" -m unittest discover -s tests "$@"
    ;;
  *)
    echo "Usage: $0 {agent|api|web|test} [args...]"
    exit 1
    ;;
esac
