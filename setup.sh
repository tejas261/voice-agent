#!/bin/sh
# Set up Listen on an Apple Silicon Mac. Run from the checkout or copy this
# script to another machine; outside a checkout it clones the private repo.
set -eu
umask 077

start_app=1
case "${1:-}" in
  '') ;;
  --no-start) start_app=0 ;;
  -h|--help)
    printf 'Usage: ./setup.sh [--no-start]\n\nInstalls Listen and starts it locally. Use --no-start to install without launching.\n'
    exit 0 ;;
  *) printf 'Unknown option: %s\nUsage: ./setup.sh [--no-start]\n' "$1" >&2; exit 2 ;;
esac
if [ "$#" -gt 1 ]; then
  printf 'Usage: ./setup.sh [--no-start]\n' >&2
  exit 2
fi

if [ "$(uname -s)" != Darwin ] || [ "$(uname -m)" != arm64 ]; then
  printf 'Full setup requires an Apple Silicon Mac: the custom voice engine uses MLX.\n' >&2
  exit 1
fi

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
if [ -f "$script_dir/requirements/voice.lock" ] && [ -f "$script_dir/run.sh" ] && [ -f "$script_dir/scripts/setup_models.py" ]; then
  project_dir=$script_dir
else
  project_dir=$(pwd)/voice-agent
  if [ -e "$project_dir" ] || [ -L "$project_dir" ]; then
    printf 'Cannot clone: %s already exists. Run setup.sh from an existing checkout instead.\n' "$project_dir" >&2
    exit 1
  fi
  if ! command -v git >/dev/null 2>&1; then
    printf 'Git is required to clone the private repository. Install Git and try again.\n' >&2
    exit 1
  fi
  printf 'Cloning the private Listen repository into %s. GitHub access may be required.\n' "$project_dir"
  git clone https://github.com/tejas261/voice-agent.git "$project_dir"
fi
cd "$project_dir"

printf '\nPreparing Listen. The first installation downloads several GB of dependencies and models.\n'
if [ -x "$project_dir/.setup-tools/bin/uv" ]; then
  uv_bin=$project_dir/.setup-tools/bin/uv
elif command -v uv >/dev/null 2>&1; then
  uv_bin=$(command -v uv)
else
  if ! command -v curl >/dev/null 2>&1; then
    printf 'curl is required to install uv. Install curl and try again.\n' >&2
    exit 1
  fi
  printf '\nInstalling uv locally...\n'
  mkdir -p "$project_dir/.setup-tools/bin"
  installer=$(mktemp "${TMPDIR:-/tmp}/listen-uv-installer.XXXXXX")
  trap 'rm -f "$installer"' EXIT
  trap 'exit 1' HUP INT TERM
  curl --fail --location --silent --show-error https://astral.sh/uv/install.sh -o "$installer"
  UV_UNMANAGED_INSTALL="$project_dir/.setup-tools/bin" sh "$installer"
  rm -f "$installer"
  trap - EXIT HUP INT TERM
  uv_bin=$project_dir/.setup-tools/bin/uv
  if [ ! -x "$uv_bin" ]; then
    printf 'uv installation did not produce an executable.\n' >&2
    exit 1
  fi
fi

ensure_env() {
  env_dir=$1
  if [ -e "$env_dir" ] || [ -L "$env_dir" ]; then
    if [ ! -f "$env_dir/pyvenv.cfg" ] || [ ! -x "$env_dir/bin/python" ]; then
      printf 'Existing environment at %s is incomplete; it was not changed.\n' "$env_dir" >&2
      exit 1
    fi
    if ! "$env_dir/bin/python" -c 'import platform,sys; sys.exit(not (sys.version_info[:2] == (3,12) and platform.system() == "Darwin" and platform.machine() == "arm64"))'; then
      printf 'Existing environment at %s must use native Apple Silicon Python 3.12; it was not changed.\n' "$env_dir" >&2
      exit 1
    fi
    printf 'Reusing %s\n' "$env_dir"
  else
    printf 'Creating %s with Python 3.12...\n' "$env_dir"
    "$uv_bin" venv --python 3.12 "$env_dir"
    if ! "$env_dir/bin/python" -c 'import platform,sys; sys.exit(not (sys.version_info[:2] == (3,12) and platform.system() == "Darwin" and platform.machine() == "arm64"))'; then
      printf 'The installed Python must be native Apple Silicon Python 3.12.\n' >&2
      exit 1
    fi
  fi
}

ensure_env "$project_dir/.venv-local"
printf '\nInstalling application and document-converter dependencies...\n'
"$uv_bin" pip install --python "$project_dir/.venv-local/bin/python" -r requirements/voice.lock -r requirements/documents.lock

if [ ! -e .env ] && [ ! -L .env ]; then
  "$project_dir/.venv-local/bin/python" - <<'PY'
import os
import secrets
from pathlib import Path

contents = Path('.env.example').read_text(encoding='utf-8')
assert contents.count('\nAPI_TOKEN=\n') == 1, 'Missing API_TOKEN setting in .env.example'
contents = contents.replace('\nAPI_TOKEN=\n', '\nAPI_TOKEN=' + secrets.token_urlsafe(32) + '\n', 1)
fd = os.open('.env', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w', encoding='utf-8') as output:
    output.write(contents)
PY
  printf 'Created a private .env with a new operator token.\n'
else
  printf 'Preserving the existing .env.\n'
fi

qwen_python=$("$project_dir/.venv-local/bin/python" -c 'from src.config import Settings; print(Settings.from_env().qwen_python)')
case "$qwen_python" in
  */bin/python) qwen_env=${qwen_python%/bin/python} ;;
  *) printf 'QWEN_PYTHON must point to a virtual environment bin/python for setup.\n' >&2; exit 1 ;;
esac
if ! "$project_dir/.venv-local/bin/python" -c 'import os,sys; sys.exit(os.path.realpath(sys.argv[1]) == os.path.realpath(sys.argv[2]))' "$qwen_env" "$project_dir/.venv-local"; then
  printf 'QWEN_PYTHON must use a separate environment from the main application.\n' >&2
  exit 1
fi
ensure_env "$qwen_env"
printf '\nInstalling the custom-voice worker...\n'
"$uv_bin" pip install --python "$qwen_python" -r requirements/qwen-mlx.lock

printf '\nPreparing speech and recognition models...\n'
RETRIEVAL_MODE=lexical "$project_dir/.venv-local/bin/python" scripts/setup_models.py
printf '\nPreparing semantic retrieval models...\n'
"$project_dir/.venv-local/bin/python" scripts/setup_retrieval.py
printf '\nPreparing the custom voice model...\n'
"$project_dir/.venv-local/bin/python" scripts/setup_qwen.py

printf '\nSetup complete. Open http://127.0.0.1:8000 after the server starts.\n'
printf 'For the internal testing screen, use the API_TOKEN in %s/.env at /internal.\n' "$project_dir"
printf 'Google sign-in, hosted answers, email and phone calls need their own service credentials; see README.md.\n'
if [ "$start_app" -eq 0 ]; then
  printf 'To start later: cd "%s" && ./run.sh\n' "$project_dir"
else
  printf 'Starting Listen. Press Ctrl+C to stop it.\n\n'
  exec ./run.sh
fi
