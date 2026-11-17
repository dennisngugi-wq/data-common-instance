#!/bin/sh

set -u
cd /project
export COMPOSE_IGNORE_ORPHANS=true
AT="${REFRESH_AT_UTC:-21:00}"
SUCCESS="Runner finished successfully"
TRIGGER="work/REFRESH_NOW"
LAST="logs/last_refresh.txt"
LOG="logs/pipeline.log"
DETAILS="logs/pipeline-details.log"
LOADER="logs/loader.log"
HISTORY="logs/history"
ALERT="logs/ALERT.txt"
ALERTS="logs/alerts.log"
STEPS=5
RULE="------------------------------------------------------------------------------"
BAR="=============================================================================="

own() { chown -R "${HOST_UID:-1000}:${HOST_GID:-1000}" "$@" 2>/dev/null || true; }
log() { echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') | scheduler | $*"; }
say() { echo "$*" >> "$LOG"; }
step() { log "step $1 of $STEPS: $2"; printf '\nSTEP %s of %s  %s\n%s\n' "$1" "$STEPS" "$2" "$RULE" >> "$LOG"; }
item() {
  case "$2" in FAILED|failed) mark="✗" ;; skipped) mark="-" ;; *) mark="✓" ;; esac
  dots=$(printf '%*s' $((33 - ${#1})) '' | tr ' ' '.')
  say "  $mark $1 $dots $2${3:+  $3}"
}
took() { s=$(( $(date -u +%s) - $1 )); [ "$s" -ge 60 ] && echo "$((s / 60))m $((s % 60))s" || echo "${s}s"; }
job() {
  name=$1; shift
  docker rm -f "$name" >/dev/null 2>&1
  printf '\n==== %s  %s\n' "$(date -u '+%Y-%m-%d %H:%M:%S UTC')" "$name" >> "$DETAILS"
  docker compose run --name "$name" -T pipeline "$@" >> "$LOG" 2>> "$DETAILS"
}
notify() { docker compose run --rm -T pipeline python automation/src/alert.py "$1" >> "$DETAILS" 2>&1 || true; }
alert() {
  mkdir -p logs
  echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') $1" > "$ALERT"
  cat "$ALERT" >> "$ALERTS"; own "$ALERT" "$ALERTS"
  log "ALERT: $1"
  notify "REFRESH ALERT: $1 Details: logs/pipeline.log"
}
clear_alert() {
  [ -f "$ALERT" ] || return 0
  rm -f "$ALERT"
  echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') RECOVERED: $1" >> "$ALERTS"; own "$ALERTS"
  notify "RECOVERED: $1"
}
finish() {
  [ -f work/run_summary.txt ] && cat work/run_summary.txt >> "$LOG"
  say ""
  say " RESULT: $1"
  say " Finished $(date -u '+%Y-%m-%d %H:%M UTC') after $(took "$START")."
  say "$BAR"
  stamp=$(date -u -d "@$START" +%Y-%m-%d_%H%M)
  cp "$LOG" "$HISTORY/$stamp.log"
  ls -1t "$HISTORY"/*.log 2>/dev/null | tail -n +61 | xargs -r rm -f
  own "$LOG" "$DETAILS" "$HISTORY"
  echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') $2" > "$LAST"; own "$LAST"
  log "$1"
  case "$2" in
    FAILED*) alert "$1" ;;
    *)
      if [ "${total:-0}" -gt 0 ] && [ $((${unreach:-0} * 2)) -ge "${total:-0}" ]; then
        alert "$unreach of $total datasets could not be downloaded (no internet, or the publisher is down); the website shows the last good data."
      else
        clear_alert "$1"
      fi ;;
  esac
}

has_key() { [ -s env.list ] && grep -q '^DC_API_KEY=.' env.list && ! grep -q 'your_key_here' env.list; }

refresh() {
  if ! has_key; then
    log "env.list with DC_API_KEY missing: copy env.list.example to env.list and add your key"
    alert "FAILED: env.list with DC_API_KEY is missing, so nothing was refreshed."
    return 1
  fi
  mkdir -p work/feed_reports local_store local_dc "$HISTORY" && own work local_store local_dc logs
  rm -f "$LOG" "$DETAILS"
  rm -f work/run_summary.txt work/local_dc_status.txt
  total=0; unreach=0
  START=$(date -u +%s)
  {
    echo "$BAR"
    echo " DATA REFRESH  ·  started $(date -u '+%Y-%m-%d %H:%M UTC')"
    echo "$BAR"
    echo " How to read this log"
    echo "   Steps 1-5 run in order; each item gets one line."
    echo "   ✓ fine    ✗ failed    ! warning    i note    - skipped"
    echo "   Everything that needs you is listed under NEEDS YOUR ATTENTION at the end."
    echo "   The rules every dataset must pass: RULES.md (codes C1-C6, E1-E9, V10, V12)"
    echo "   Technical details (retries, tracebacks, Docker): logs/pipeline-details.log"
    echo "   Earlier runs: logs/history/"
  } >> "$LOG"

  step 1 "Read the links (config/sheets.json, data/inbox/) and download the sheets"
  job dcproject-sync-registries python automation/src/run_pipeline.py --sync-only
  feeds=$(docker compose run --rm -T pipeline python automation/src/run_pipeline.py --list-feeds --no-sync 2>>"$DETAILS")
  total=$(echo "$feeds" | wc -w | tr -d ' ')
  rm -f work/feed_reports/*.json

  step 2 "Check and prepare each dataset ($total)"
  rc=0; n=0; bad=0
  for fid in $feeds; do
    n=$((n + 1))
    if ! job "dcproject-data-$(echo "$fid" | tr '_' '-')" python automation/src/run_pipeline.py \
        --feed "$fid" --no-sync --no-schema --position "$(printf '%3d/%d' "$n" "$total")"; then
      bad=$((bad + 1)); rc=1
    fi
  done
  for c in $(docker ps -a --format '{{.Names}}' --filter "name=^dcproject-data-"); do
    f=$(echo "${c#dcproject-data-}" | tr '-' '_')
    echo "$feeds" | grep -qx "$f" || docker rm -f "$c" >/dev/null 2>&1
  done
  unreach=$(grep -l '"E1 ' work/feed_reports/*.json 2>/dev/null | wc -l | tr -d ' ')
  say ""
  say "  $n dataset(s) checked, $bad failed (a dataset that breaks a rule in RULES.md is not on the website;"
  say "  one whose source could not be reached keeps its last good version)"

  step 3 "Combine the datasets and update the website files"
  job dcproject-finalize sh -c 'python automation/src/run_pipeline.py --finalize --no-sync; r=$?;
    python automation/src/build_local_dc.py || exit 2; python automation/src/build_place_index.py;
    python automation/src/build_site_catalog.py; python automation/src/build_homepage.py; exit $r'
  frc=$?
  [ "$frc" -eq 2 ] && rc=2
  [ "$frc" -ne 0 ] && [ "$rc" -eq 0 ] && rc=1
  if [ "$rc" -eq 2 ]; then
    item "Database input files" "FAILED" "could not combine the datasets (see logs/pipeline-details.log)"
    step 4 "Load the data into the database"
    item "Database" "skipped" "nothing to load"
    step 5 "Website"
    item "Website" "skipped" "keeps serving the current database"
    finish "FAILED: the datasets could not be combined; the website was not changed." "FAILED pipeline exit 2"
    return 1
  fi

  step 4 "Load the data into the database"
  if [ "$(cat work/local_dc_status.txt 2>/dev/null)" = "UNCHANGED" ] && [ -f local_dc_db/datacommons/datacommons.db ]; then
    item "Database" "skipped" "the data did not change, so the current database is kept"
    step 5 "Website"
    docker compose --profile site up -d website >/dev/null 2>>"$DETAILS"
    item "Website" "ok" "running, unchanged · http://localhost:${SITE_PORT:-8090}"
    finish "OK, nothing new to publish (pipeline exit $rc)." "OK unchanged (pipeline exit $rc)"
    return 0
  fi
  t=$(date -u +%s)
  rm -rf local_dc_db_next && mkdir -p local_dc_db_next
  docker compose run --rm loader > "$LOADER" 2>&1
  own "$LOADER"
  if ! grep -q "$SUCCESS" "$LOADER" || [ ! -f local_dc_db_next/datacommons/datacommons.db ]; then
    item "Database" "FAILED" "the live database was left unchanged ($(took "$t"))"
    say "      last error lines from $LOADER:"
    grep -iE 'error|exception|failed' "$LOADER" | tail -n 5 | cut -c1-150 | sed 's/^/        /' >> "$LOG"
    step 5 "Website"
    item "Website" "ok" "keeps serving the previous database"
    finish "FAILED: the database could not be built; the website still shows the previous data." "FAILED load"
    return 1
  fi
  item "Database" "updated" "built in $(took "$t") · full log: $LOADER"

  step 5 "Website"
  docker compose --profile site stop website >/dev/null 2>>"$DETAILS"
  rm -rf local_dc_db_prev
  [ -d local_dc_db ] && mv local_dc_db local_dc_db_prev
  mv local_dc_db_next local_dc_db
  docker compose --profile site up -d website >/dev/null 2>>"$DETAILS"
  item "Website" "updated" "restarted with the new database · http://localhost:${SITE_PORT:-8090}"
  finish "OK, the website now shows the new data (pipeline exit $rc)." "OK loaded (pipeline exit $rc)"
}

seconds_until_next_run() {
  now=$(date -u +%s)
  target=$(date -u -d "$(date -u +%Y-%m-%d) $AT:00" +%s)
  [ "$target" -le "$now" ] && target=$((target + 86400))
  echo $((target - now))
}

log "started; daily refresh at $AT UTC; refresh on demand: touch $TRIGGER (scripts/refresh-now.sh); log: $LOG"
if ! has_key; then
  log "waiting for env.list with DC_API_KEY (copy env.list.example to env.list and add your key)"
  until has_key; do sleep 60; done
fi
[ -f local_dc_db/datacommons/datacommons.db ] && docker compose --profile site up -d website
if [ "${REFRESH_ON_START:-true}" = "true" ] || [ ! -f local_dc_db/datacommons/datacommons.db ]; then
  refresh || log "refresh failed"
fi
while true; do
  wait_s=$(seconds_until_next_run)
  due=$(( $(date -u +%s) + wait_s ))
  log "next refresh in $((wait_s / 3600))h $((wait_s % 3600 / 60))m"
  while [ "$(date -u +%s)" -lt "$due" ]; do
    if [ -f "$TRIGGER" ]; then
      rm -f "$TRIGGER"
      log "on-demand refresh requested"
      break
    fi
    sleep 30
  done
  refresh || log "refresh failed"
done
