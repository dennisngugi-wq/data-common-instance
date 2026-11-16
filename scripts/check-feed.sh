#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
args=(--check)
for f in "$@"; do args+=(--feed "$f"); done
export DOCKER_CONTEXT="${DOCKER_CONTEXT:-default}"
docker compose --profile jobs build -q pipeline >/dev/null
HOST_PROJECT_DIR="$PWD" HOST_UID="$(id -u)" HOST_GID="$(id -g)" \
  docker compose run --rm pipeline python automation/src/run_pipeline.py "${args[@]}"
echo "Full report: work/validation_summary.json   Croissant preview: work/croissant/"
