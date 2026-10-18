#!/usr/bin/env bash
# Start Crafts Prepare on the loopback interface.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if ! command -v python3 >/dev/null; then
  echo "Python 3.12, 3.13, or 3.14 is required." >&2
  exit 1
fi
python3 -c 'import sys; raise SystemExit(0 if (3, 12) <= sys.version_info[:2] <= (3, 14) else 1)' || {
  echo "This Python is $(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])'). Crafts Prepare supports 3.12 through 3.14." >&2
  exit 1
}
if ! command -v ffmpeg >/dev/null || ! command -v ffprobe >/dev/null; then
  echo "Install ffmpeg and ffprobe yourself. Crafts Prepare does not download them." >&2
  exit 1
fi

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install -r backend/requirements.lock

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
