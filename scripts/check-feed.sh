#!/usr/bin/env bash
# Try one feed file without publishing anything: fetch, map, validate, print the report.
# Works for feeds that are still enabled: false.
#
#   bash scripts/check-feed.sh my_feed          (config/feeds/my_feed.yaml)
#   bash scripts/check-feed.sh                   (every enabled feed)
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
args=(--check)
for f in "$@"; do args+=(--feed "$f"); done
export DOCKER_CONTEXT="${DOCKER_CONTEXT:-default}"
docker compose --profile jobs build -q pipeline >/dev/null
HOST_PROJECT_DIR="$PWD" HOST_UID="$(id -u)" HOST_GID="$(id -g)" \
  docker compose run --rm pipeline python automation/src/run_pipeline.py "${args[@]}"
echo "Full report: work/validation_summary.json   Croissant preview: work/croissant/"
