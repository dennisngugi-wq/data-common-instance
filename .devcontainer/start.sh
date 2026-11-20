#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [ -z "${DC_API_KEY:-}" ]; then
  echo "DC_API_KEY is not set. Add it as a Codespaces secret (repository Settings > Secrets and variables >"
  echo "Codespaces, name DC_API_KEY), then rebuild the codespace."
  exit 0
fi
printf 'DC_API_KEY=%s\nMAPS_API_KEY=not-configured\n' "$DC_API_KEY" > env.list

SITE="https://${CODESPACE_NAME}-8090.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-app.github.dev}"
{
  echo "HOST_PROJECT_DIR=$PWD"
  echo "SITE_PORT=8090"
  echo "REFRESH_AT_UTC=21:00"
  echo "REFRESH_ON_START=true"
  echo "SITE_URL=$SITE"
} > .env

until docker info >/dev/null 2>&1; do sleep 2; done
nohup bash scripts/start.sh > logs-start.txt 2>&1 &
echo "Starting the site (the first load takes 20-40 minutes): tail -f logs/pipeline.log"
echo "Share it: Ports tab > 8090 > right-click > Port visibility > Public, then send $SITE"
