#!/usr/bin/env bash
# One-command launcher for the Tripwire app on macOS / Linux.
# First run: creates .venv, installs the app, builds the web UI. Opens
# http://127.0.0.1:8765.   ./run_app.sh --demo   |   ./run_app.sh --rebuild-ui
set -euo pipefail
cd "$(dirname "$0")"

command -v python3 >/dev/null || { echo "Python 3.11+ is required: https://python.org"; exit 1; }

if [ ! -x .venv/bin/python ]; then
  echo "[1/3] Creating virtual environment..."
  python3 -m venv .venv
else
  echo "[1/3] Virtual environment already exists."
fi
PY=.venv/bin/python

echo "[2/3] Installing the app (first run only takes a minute)..."
$PY -m pip install --quiet --upgrade pip
$PY -m pip install --quiet -e ".[app]"

REBUILD=0
if [ "${1:-}" = "--rebuild-ui" ]; then REBUILD=1; shift; fi
[ -f web/out/index.html ] || REBUILD=1
if [ "$REBUILD" = 1 ]; then
  if command -v npm >/dev/null; then
    echo "[3/3] Building the web UI..."
    (cd web && npm install --no-audit --no-fund && npm run build)
  else
    echo "[3/3] Node.js not found - skipping the UI build (install Node 20+ from https://nodejs.org)."
  fi
else
  echo "[3/3] Web UI already built."
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example - fill it in when you connect accounts. See docs/CONNECT.md"
fi

exec $PY -m packages.app "$@"
