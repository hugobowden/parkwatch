#!/usr/bin/env bash
# Start ParkWatch at http://localhost:8000 (set HOST=0.0.0.0 to reach it from other devices).
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "Setting up Python environment..."
  if command -v uv >/dev/null; then
    uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt
  else
    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
  fi
fi
exec .venv/bin/uvicorn app.main:app --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}"
