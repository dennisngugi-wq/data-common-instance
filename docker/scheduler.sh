#!/bin/sh
# Runs inside the `scheduler` container (docker:27-cli) and drives the other containers.
#
# At start-up (REFRESH_ON_START), every day at REFRESH_AT_UTC (21:00 UTC = 00:00 Africa/Nairobi), and whenever
# work/REFRESH_NOW exists (scripts/refresh-now.sh):
#   1. pipeline  fetch every feed, validate, publish to local_store, merge into local_dc   (prints CHANGED/UNCHANGED)
#   2. loader    only if local_dc changed or there is no database yet; loads into local_dc_db_next/
#   3. swap      only after "Runner finished successfully": db -> db_prev, db_next -> db
#   4. restart   the website so it serves the new database
# A rejected feed keeps its last published version; a failed load leaves the live database untouched.

set -u
cd /project
AT="${REFRESH_AT_UTC:-21:00}"
SUCCESS="Runner finished successfully"
TRIGGER="work/REFRESH_NOW"
LAST="work/last_refresh.txt"

own() { chown -R "${HOST_UID:-1000}:${HOST_GID:-1000}" "$@" 2>/dev/null || true; }
log() { echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') | scheduler | $*"; }
# Run one pipeline job as a named container of the main instance; output appended to work/pipeline.log.
job() {
  name=$1; shift
  docker rm -f "$name" >/dev/null 2>&1
  docker compose run --name "$name" -T pipeline "$@" >> work/pipeline.log 2>&1
}

has_key() { [ -s env.list ] && grep -q '^DC_API_KEY=.' env.list && ! grep -q 'your_key_here' env.list; }

refresh() {
  if ! has_key; then
    log "env.list with DC_API_KEY missing: copy env.list.example to env.list and add your key"
    return 1
  fi
  mkdir -p work/feed_reports local_store local_dc && own work local_store local_dc
  : > work/pipeline.log
  # One main instance (compose project "dcproject"), one container per job, kept after it exits so Docker Desktop
  # / Portainer list every dataset under the instance with its own status and logs:
  #   dcproject-sync-registries   mirror every tab of every registry sheet
  #   dcproject-data-<feed>       fetch, validate and publish ONE dataset (a new dataset = a new container)
  #   dcproject-finalize          shared schema, run report, knowledge base, merge for the loader, place index
  log "1/4 pipeline: sync registries, one container per dataset, finalize"
  job dcproject-sync-registries python automation/src/run_pipeline.py --sync-only
  feeds=$(docker compose run --rm -T pipeline python automation/src/run_pipeline.py --list-feeds --no-sync 2>/dev/null)
  rm -f work/feed_reports/*.json
  rc=0; n=0; bad=0
  for fid in $feeds; do
    n=$((n + 1))
    if ! job "dcproject-data-$(echo "$fid" | tr '_' '-')" python automation/src/run_pipeline.py \
        --feed "$fid" --no-sync --no-schema; then
      bad=$((bad + 1)); rc=1
    fi
  done
  # containers of datasets that are no longer configured
  for c in $(docker ps -a --format '{{.Names}}' --filter "name=^dcproject-data-"); do
    f=$(echo "${c#dcproject-data-}" | tr '-' '_')
    echo "$feeds" | grep -qx "$f" || docker rm -f "$c" >/dev/null 2>&1
  done
  log "$n dataset container(s) ran, $bad rejected (their last good version stays live)"
  job dcproject-finalize sh -c 'python automation/src/run_pipeline.py --finalize --no-sync; r=$?;
    python automation/src/build_local_dc.py || exit 2; python automation/src/build_place_index.py;
    python automation/src/build_homepage.py; python automation/src/build_site_catalog.py; exit $r'
  frc=$?
  [ "$frc" -eq 2 ] && rc=2
  [ "$frc" -ne 0 ] && [ "$rc" -eq 0 ] && rc=1
  own work/pipeline.log
  if [ "$rc" -eq 2 ]; then
    log "merge failed (nothing published yet?); nothing to load"
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') FAILED pipeline exit 2" > "$LAST"; own "$LAST"
    return 1
  fi
  [ "$rc" -ne 0 ] && log "pipeline exit $rc: some dataset was rejected (see work/validation_summary.json); loading what is published"

  tail -n 40 work/pipeline.log
  if grep -q '^UNCHANGED' work/pipeline.log && [ -f local_dc_db/datacommons/datacommons.db ]; then
    log "data unchanged; website keeps serving the current database"
    docker compose --profile site up -d website >/dev/null 2>&1
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') OK unchanged (pipeline exit $rc)" > "$LAST"; own "$LAST"
    return 0
  fi

  log "2/4 loader: local_dc -> local_dc_db_next"
  rm -rf local_dc_db_next && mkdir -p local_dc_db_next
  docker compose run --rm loader > work/loader.log 2>&1
  own work/loader.log
  tail -n 25 work/loader.log
  if ! grep -q "$SUCCESS" work/loader.log || [ ! -f local_dc_db_next/datacommons/datacommons.db ]; then
    log "LOAD FAILED: live database left unchanged (full log: work/loader.log)"
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') FAILED load" > "$LAST"; own "$LAST"
    return 1
  fi

  log "3/4 swap databases"
  docker compose --profile site stop website >/dev/null 2>&1
  rm -rf local_dc_db_prev
  [ -d local_dc_db ] && mv local_dc_db local_dc_db_prev
  mv local_dc_db_next local_dc_db

  log "4/4 start website"
  docker compose --profile site up -d website
  echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') OK loaded (pipeline exit $rc)" > "$LAST"; own "$LAST"
  log "refresh complete: http://localhost:${SITE_PORT:-8090}"
}

seconds_until_next_run() {
  now=$(date -u +%s)
  target=$(date -u -d "$(date -u +%Y-%m-%d) $AT:00" +%s)
  [ "$target" -le "$now" ] && target=$((target + 86400))
  echo $((target - now))
}

log "started; daily refresh at $AT UTC; refresh on demand: touch $TRIGGER (scripts/refresh-now.sh)"
if ! has_key; then
  log "waiting for env.list with DC_API_KEY (copy env.list.example to env.list and add your key)"
  until has_key; do sleep 60; done
fi
# Serve the existing database straight away.
[ -f local_dc_db/datacommons/datacommons.db ] && docker compose --profile site up -d website
if [ "${REFRESH_ON_START:-true}" = "true" ] || [ ! -f local_dc_db/datacommons/datacommons.db ]; then
  refresh || log "refresh failed"
fi
while true; do
  wait_s=$(seconds_until_next_run)
  log "next refresh in $((wait_s / 3600))h $((wait_s % 3600 / 60))m"
  # Sleep in one-minute steps so an on-demand refresh (work/REFRESH_NOW) starts within a minute.
  while [ "$wait_s" -gt 0 ]; do
    if [ -f "$TRIGGER" ]; then
      rm -f "$TRIGGER"
      log "on-demand refresh requested"
      break
    fi
    step=60; [ "$wait_s" -lt 60 ] && step=$wait_s
    sleep "$step"
    wait_s=$((wait_s - step))
  done
  refresh || log "refresh failed"
done
