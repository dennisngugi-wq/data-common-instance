#!/usr/bin/env bash
# A copy of this Data Commons for Docker Desktop: one container, "datacommon-project", that you start and stop with
# the ▶ / ■ buttons in Docker Desktop (Containers). No command line needed after this script.
#
#   bash scripts/desktop-copy.sh            create (or refresh) the copy with today's data
#   bash scripts/desktop-copy.sh --start    ... and start it now
#   bash scripts/desktop-copy.sh --clean    also delete the bare containers made with "Run" on the
#                                           datacommons-services image (they have no key or data and always fail)
#
# Why not just "Run" on the image? The image alone has no API key, no data and none of this site's settings, so it
# stops with "DC_API_KEY not specified". This container is made from the same image WITH the key (from env.list,
# kept in the container's settings, never in an image), this site's settings and a COPY of the data.
#
# Safe for the main instance: the main instance keeps running on Docker Engine (http://localhost:8090, refreshed every
# night). The Docker Desktop copy serves http://localhost:8091 from desktop/dc_db, a snapshot taken when this script
# runs; nothing it does reaches local_store/ or local_dc_db/. Run the script again to bring it up to date.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
P="$PWD"
CTX=desktop-linux
NAME=datacommon-project
PORT="${DESKTOP_PORT:-8091}"
IMAGE=gcr.io/datcom-ci/datacommons-services:stable
START=false; CLEAN=false
for a in "$@"; do
  case "$a" in --start) START=true ;; --clean) CLEAN=true ;; *) echo "unknown option $a"; exit 2 ;; esac
done

d() { DOCKER_CONTEXT=$CTX docker "$@"; }

if ! d info >/dev/null 2>&1; then
  echo "Docker Desktop is not running. Open Docker Desktop, wait until it says 'Engine running', then run this again."
  exit 1
fi
[ -f env.list ] && grep -qE '^\s*DC_API_KEY=.+' env.list || { echo "env.list has no DC_API_KEY (copy env.list.example)"; exit 1; }
[ -d local_dc_db/datacommons ] || { echo "No database yet: start the main instance first (bash scripts/start.sh)"; exit 1; }

if $CLEAN; then
  bare=$(d ps -a --filter "ancestor=$IMAGE" --filter status=exited --format '{{.Names}} {{.Labels}}' |
         grep -v "datacommon.copy=desktop" | awk '{print $1}' || true)
  for c in $bare; do d rm "$c" >/dev/null && echo "removed bare container $c"; done
fi

echo "Copying today's data to desktop/ (the main instance is not touched)..."
d rm -f "$NAME" >/dev/null 2>&1 || true
mkdir -p desktop/dc_db
rm -rf desktop/dc_db.new && cp -a local_dc_db desktop/dc_db.new && rm -rf desktop/dc_db && mv desktop/dc_db.new desktop/dc_db
rm -rf desktop/dc_input && cp -a local_dc desktop/dc_input
for f in site/static site/templates site/app_env site/config config/instance.yaml; do
  mkdir -p "desktop/$(dirname "$f")"; rm -rf "desktop/$f"; cp -a "$f" "desktop/$f"
done

d image inspect "$IMAGE" >/dev/null 2>&1 || { echo "Downloading the Data Commons image into Docker Desktop (once)..."; d pull "$IMAGE"; }

D="$P/desktop"
d create --name "$NAME" \
  --label com.docker.compose.project=datacommon-project-desktop --label com.docker.compose.service=website \
  --label datacommon.copy=desktop \
  --platform linux/amd64 --env-file env.list \
  -e INPUT_DIR=/data -e OUTPUT_DIR=/out -e USE_SQLITE=true -e DISABLE_GOOGLE_MAPS=true \
  -e SITE_URL="http://localhost:$PORT" \
  -v "$D/dc_input:/data:ro" -v "$D/dc_db:/out" \
  -v "$D/site/app_env/custom.py:/workspace/server/app_env/custom.py:ro" \
  -v "$D/site/templates:/workspace/server/templates/custom_dc/custom:ro" \
  -v "$D/site/static:/workspace/server/dist/custom_dc/custom:ro" \
  -v "$D/config/instance.yaml:/workspace/instance.yaml:ro" \
  -v "$D/site/config/home_page/sample_questions.json:/workspace/server/config/home_page/sample_questions.json:ro" \
  -v "$D/site/config/home_page/partners.json:/workspace/server/config/home_page/partners.json:ro" \
  -v "$D/site/config/feature_flags/custom.json:/workspace/server/config/feature_flag_configs/custom.json:ro" \
  -v "$D/site/config/header.json:/workspace/server/config/custom_dc/custom/base/header.json:ro" \
  -p "$PORT:8080" "$IMAGE" >/dev/null
echo "Created '$NAME' in Docker Desktop (data snapshot of $(date '+%Y-%m-%d %H:%M'))."
if $START; then
  d start "$NAME" >/dev/null && echo "Started: http://localhost:$PORT (ready in about a minute)"
else
  echo "In Docker Desktop: Containers -> $NAME -> ▶ (Start), then open http://localhost:$PORT"
fi
