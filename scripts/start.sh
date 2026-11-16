#!/usr/bin/env bash
set -euo pipefail

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT"
export HOST_PROJECT_DIR="$PROJECT"
export HOST_UID="$(id -u)" HOST_GID="$(id -g)"
export DOCKER_CONTEXT="${DOCKER_CONTEXT:-default}"
PORT="$(grep -s '^SITE_PORT=' .env | cut -d= -f2)"; PORT="${PORT:-8090}"

if ! docker info >/dev/null 2>&1; then
  echo "Docker is not running; starting it (asks for your password)..."
  sudo systemctl start docker
fi
[ -f env.list ] || { echo "env.list missing: cp env.list.example env.list and add DC_API_KEY"; exit 1; }
[ -f .env ] || cp .env.example .env
mkdir -p work local_store local_dc local_dc_db
if ! grep -q '^KNOWLEDGE_HOST_DIR=' .env && [ ! -d "$PROJECT/../../mcp agent/knowledge" ]; then
  mkdir -p work/knowledge
  echo "KNOWLEDGE_HOST_DIR=$PROJECT/work/knowledge" >> .env
fi

case "${1:-}" in
  --stop)
    docker compose --profile site --profile jobs down
    echo "Stopped. Data folders are kept."
    exit 0 ;;
  --site)
    docker compose --profile site up -d website ;;
  *)
    docker compose --profile jobs build pipeline
    [ -f local_dc_db/datacommons/datacommons.db ] && docker compose --profile site up -d website
    docker compose up -d scheduler
    echo "Refresh running: tail -f logs/pipeline.log" ;;
esac

echo -n "Waiting for http://localhost:$PORT "
for _ in $(seq 1 100); do
  if curl -fs -o /dev/null "http://localhost:$PORT/"; then echo " up."; exit 0; fi
  echo -n "."; sleep 6
done
echo " not up yet (a first load takes 5-15 min): tail -f logs/pipeline.log"
