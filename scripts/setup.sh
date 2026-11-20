#!/usr/bin/env bash
# One-time setup on macOS/Linux: venv + deps + workspace. Usage: bash scripts/setup.sh
set -euo pipefail
SKILL="$(cd "$(dirname "$0")/.." && pwd)"
command -v ffmpeg >/dev/null || echo "WARNING: ffmpeg not found (brew install ffmpeg / apt install ffmpeg)"
PY="$(command -v python3 || command -v python)"
[ -d "$SKILL/.venv" ] || "$PY" -m venv "$SKILL/.venv"
"$SKILL/.venv/bin/python" -m pip install -q --upgrade pip
"$SKILL/.venv/bin/python" -m pip install -q -r "$SKILL/requirements.txt"
cd "$SKILL" && "$SKILL/.venv/bin/python" -m gta6clips init
echo "Setup done. Fill in the .env shown above, then run: scripts/gta6.sh doctor"
