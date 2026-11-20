#!/usr/bin/env bash
# Run any pipeline command with the skill's venv. Example: scripts/gta6.sh once
SKILL="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SKILL" && exec "$SKILL/.venv/bin/python" -m gta6clips "$@"
