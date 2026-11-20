#!/usr/bin/env bash
# Add a cron entry that runs one pipeline cycle every N minutes (default 30).
# Usage: bash scripts/schedule_cron.sh [minutes] | bash scripts/schedule_cron.sh remove
set -euo pipefail
SKILL="$(cd "$(dirname "$0")/.." && pwd)"
WS="${GTA6_SHORTS_HOME:-$HOME/gta6-shorts}"
TAG="# gta6-shorts"
if [ "${1:-}" = "remove" ]; then
  crontab -l 2>/dev/null | grep -v "$TAG" | crontab -; echo "removed"; exit
fi
MIN="${1:-30}"
LINE="*/$MIN * * * * GTA6_SHORTS_HOME=$WS $SKILL/scripts/gta6.sh once >> $WS/logs/cron.log 2>&1 $TAG"
(crontab -l 2>/dev/null | grep -v "$TAG"; echo "$LINE") | crontab -
echo "scheduled every $MIN min (workspace $WS)"
