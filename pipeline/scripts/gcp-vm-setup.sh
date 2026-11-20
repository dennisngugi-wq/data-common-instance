#!/usr/bin/env bash
set -euo pipefail

APP="$HOME/datacommon-project"
BUNDLE="$HOME/dcproject.tgz"
[ -f "$BUNDLE" ] || { echo "$BUNDLE not found: run scripts/gcp-deploy.sh from your computer"; exit 1; }

if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sudo sh
fi
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"

busy="$(sudo docker ps --format '{{.Names}}' | grep -E '^dcproject-(data-|sync-registries|finalize)|loader' || true)"
if [ -n "$busy" ]; then
  echo "A refresh is running on the VM ($(echo "$busy" | head -n 1)); nothing was changed. Try again in 15 minutes."
  exit 1
fi
had_scheduler="$(sudo docker ps -q --filter name=dcproject-scheduler)"

mkdir -p "$APP"
rm -rf "$APP/imports/data/inbox"
tar -xzf "$BUNDLE" -C "$APP"
rm -f "$BUNDLE"
chmod 600 "$APP/env.list"
cd "$APP"
{
  echo "HOST_PROJECT_DIR=$APP"
  echo "SITE_PORT=80"
  echo "REFRESH_AT_UTC=${REFRESH_AT_UTC:-21:00}"
  echo "REFRESH_ON_START=true"
  echo "SITE_URL=${SITE_URL:-}"
} > .env

sg docker -c "bash pipeline/scripts/start.sh" || true
if [ -n "$had_scheduler" ]; then
  sudo docker restart "$(sudo docker ps -q --filter name=dcproject-scheduler)" >/dev/null
  echo "Scheduler restarted with the new code; a refresh has started."
fi
sudo docker ps --filter name=dcproject-scheduler --format 'Scheduler: {{.Status}}'
