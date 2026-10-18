#!/usr/bin/env bash
# Start Crafts Prepare on the loopback interface.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if ! command -v python3 >/dev/null; then
  echo "Python 3.11+ is required." >&2
  exit 1
fi
if ! command -v ffmpeg >/dev/null || ! command -v ffprobe >/dev/null; then
  echo "Install ffmpeg and ffprobe yourself. Crafts Prepare does not download them." >&2
  exit 1
fi

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install -r backend/requirements.txt

if [[ ! -f frontend/dist/index.html ]]; then
  if ! command -v npm >/dev/null; then
    echo "npm is required to build the web UI the first time." >&2
    exit 1
  fi
  (cd frontend && npm ci && npm run build)
fi

export CRAFTS_HOST=127.0.0.1
export CRAFTS_PORT="${CRAFTS_PORT:-8765}"
cd "$ROOT/backend"
exec python -m app
