#!/usr/bin/env bash
set -uo pipefail
PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT"
export DOCKER_CONTEXT="${DOCKER_CONTEXT:-default}"
PORT="$(grep -s '^SITE_PORT=' .env | cut -d= -f2)"; PORT="${PORT:-8090}"
URL="http://localhost:$PORT"

if [ "${1:-}" = "--install" ]; then
  ICON="$HOME/.local/share/icons/datacommon-project.svg"
  APP="$HOME/.local/share/applications/datacommon-project.desktop"
  mkdir -p "$(dirname "$ICON")" "$(dirname "$APP")"
  cp pipeline/site/static/logo.svg "$ICON"
  cat > "$APP" <<EOF
[Desktop Entry]
Type=Application
Name=DataCommon Project
Comment=Start the local Data Commons and open it ($URL)
Exec=bash "$PROJECT/pipeline/scripts/launch.sh"
Icon=$ICON
Terminal=false
Categories=Science;
EOF
  chmod +x "$APP"
  desk="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
  if [ -d "$desk" ]; then
    cp "$APP" "$desk/" && chmod +x "$desk/datacommon-project.desktop"
    gio set "$desk/datacommon-project.desktop" metadata::trusted true 2>/dev/null || true
  fi
  echo "Installed: search 'DataCommon Project' in the app menu (Desktop icon: right-click > Allow Launching if asked)."
  exit 0
fi

mkdir -p pipeline/work
exec >>pipeline/work/launch.log 2>&1
echo "== $(date '+%F %T') launch"
note() { echo "$1"; command -v notify-send >/dev/null && notify-send -i "$PROJECT/pipeline/site/static/logo.svg" "DataCommon Project" "$1" || true; }

if curl -fs -o /dev/null "$URL/"; then
  docker compose up -d scheduler >/dev/null 2>&1 || true
else
  note "Starting... the site opens when it is ready (1-2 min; first load 15-30 min)."
  if ! docker info >/dev/null 2>&1; then
    note "Docker is not running. Run: sudo systemctl start docker"; exit 1
  fi
  bash pipeline/scripts/start.sh || { note "Not up yet: see pipeline/work/launch.log or pipeline/logs/pipeline.log"; exit 1; }
fi
[ -f pipeline/logs/ALERT.txt ] && note "Refresh problem: $(cut -d' ' -f2- pipeline/logs/ALERT.txt)"
xdg-open "$URL" >/dev/null 2>&1 &
