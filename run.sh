#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv-local/bin/python ]; then
  echo "Create .venv-local and install requirements/voice.lock first. See README.md."
  exit 1
fi
export VOICE_ENABLED="${VOICE_ENABLED:-true}"
exec .venv-local/bin/python -m uvicorn src.app:app --host 127.0.0.1 --port "${PORT:-8000}" --no-proxy-headers
