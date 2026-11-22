#!/usr/bin/env bash
# Exercise remote/apply.sh against a scratch tree with a fake `docker` and a fake `sudo` on PATH.
# No ssh, no real container, no real save.
#
#   tests/test_apply_sh.sh        → one line per case, non-zero exit if any case fails
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
APPLY="$HERE/../remote/apply.sh"
PASS=0
FAIL=0

ok() { echo "ok   - $1"; PASS=$((PASS + 1)); }
no() { echo "FAIL - $1"; FAIL=$((FAIL + 1)); }

setup() {
    T="$(mktemp -d)"
    CALLS="$T/calls.log"
    mkdir -p "$T/bin" "$T/state" "$T/work" "$T/backups" "$T/data"
    : > "$CALLS"
    cat > "$T/state/campaign.json" <<'JSON'
{"identity":{"user_id":"1","handle_name":"Rain"},"rank":{"level":3},"roster":[{"id":1},{"id":2}],"currencies":{"gil":5},"meta":{"slot":"campaign"}}
JSON
    cat > "$T/staging.json" <<'JSON'
{"identity":{"user_id":"1","handle_name":"Rain"},"rank":{"level":3},"roster":[{"id":1},{"id":2},{"id":3}],"currencies":{"gil":6},"meta":{"slot":"campaign"}}
JSON
    printf '{"has_save":true,"name":"Rain","rank":3}\n' > "$T/status.json"

    cat > "$T/bin/sudo" <<'SH'
#!/bin/sh
[ "${1:-}" = "-n" ] && shift
exec "$@"
SH

    cat > "$T/bin/docker" <<'SH'
#!/bin/sh
echo "docker $*" >> "$FAKE_LOG"
case "$1" in
  stop|start) exit 0 ;;
  exec) cat "$FAKE_STATUS"; exit 0 ;;
  run)
    case "$*" in
      *"agent.py apply"*)
        echo "AGENT-APPLY" >> "$FAKE_LOG"
        [ -n "${FAKE_SLOW:-}" ] && sleep "$FAKE_SLOW"
        cp "$FAKE_STAGING" "$FAKE_WORK/staging.json"
        printf '{"currencies":{},"roster_added":%s}' "${FAKE_ADDED:-1}" > "$FAKE_WORK/expected.json"
        exit "${FAKE_APPLY_CODE:-0}" ;;
      *"agent.py validate"*)
        echo "AGENT-VALIDATE" >> "$FAKE_LOG"
        exit "${FAKE_VALIDATE_CODE:-0}" ;;
    esac
    exit 0 ;;
esac
exit 0
SH
    chmod +x "$T/bin/sudo" "$T/bin/docker"

    E=(PATH="$T/bin:$PATH"
       FFBE_SAVE="$T/state/campaign.json"
       FFBE_BACKUP_DIR="$T/backups"
       FFBE_LOCK="$T/admin.lock"
       FFBE_DATA_DIR="$T/data"
       FFBE_STATUS_TIMEOUT="${FFBE_STATUS_TIMEOUT:-2}"
       FAKE_LOG="$CALLS" FAKE_STATUS="$T/status.json" FAKE_STAGING="$T/staging.json"
       FAKE_WORK="$T/work" FAKE_ADDED="${FAKE_ADDED:-1}")
}

cleanup() { rm -rf "$T"; }

run_apply() { env "${E[@]}" bash "$APPLY" "$T/work"; }

sha() { sha256sum "$1" | cut -d' ' -f1; }
started() { grep -q '^docker start' "$CALLS"; }
stopped_once() { [ "$(grep -c '^docker stop' "$CALLS")" = "1" ]; }
live_is_staging() { cmp -s "$T/state/campaign.json" "$T/staging.json"; }
backup_count() { ls -1 "$T/backups"/campaign-*.json 2>/dev/null | wc -l; }
sequence() { grep -oE '^(docker (stop|start)|AGENT-APPLY|AGENT-VALIDATE)' "$CALLS" \
                 | sed 's/^docker //' | tr '\n' ',' ; }

# ------------------------------------------------------------------------------------ happy path
setup
if run_apply > "$T/out" 2>&1; then ok "happy path exits 0"; else no "happy path exits 0 ($(tail -2 "$T/out"))"; fi
if live_is_staging; then ok "happy path replaces the save with the staging copy"; else no "happy path replaces the save"; fi
if [ "$(backup_count)" = "1" ]; then ok "happy path leaves exactly one backup"; else no "happy path backup count"; fi
if [ ! -e "$T/state/campaign.json.tmp" ]; then ok "no temp file left behind"; else no "no temp file left behind"; fi
seq="$(sequence)"
if [ "$seq" = "stop,AGENT-APPLY,AGENT-VALIDATE,start," ]; then
    ok "call order: stop → agent → validate → start"
else
    no "call order was: $seq"
fi
stopped_once && ok "the container is stopped exactly once" || no "the container is stopped exactly once"
grep -q "agent.py apply .*/work/save.json" "$CALLS" \
    && ok "the agent edits the copy, not the live save" || no "the agent edits the copy"
grep -q "agent.py validate .*--baseline /work/baseline.json" "$CALLS" \
    && ok "validation runs against the backup" || no "validation runs against the backup"
grep -qE 'cp .* campaign\.json$' "$CALLS" \
    && no "a cp wrote the live save" || ok "nothing cps directly onto the live save"
grep -q '^docker exec' "$CALLS" && ok "the server status is polled" || no "the server status is polled"
cleanup

# ------------------------------------------------------------------------------- agent failure
setup
before="$(sha "$T/state/campaign.json")"
export FAKE_APPLY_CODE=2
if ! run_apply > "$T/out" 2>&1; then ok "agent failure exits non-zero"; else no "agent failure exits non-zero"; fi
[ "$(sha "$T/state/campaign.json")" = "$before" ] && ok "agent failure leaves the save untouched" || no "agent failure leaves the save untouched"
started && ok "agent failure starts the container again" || no "agent failure starts the container again"
grep -q "ROLLBACK" "$T/out" && ok "agent failure logs a rollback" || no "agent failure logs a rollback"
[ "$(grep -c '^docker start' "$CALLS")" = "1" ] && ok "rollback starts exactly once" || no "rollback starts exactly once"
unset FAKE_APPLY_CODE
cleanup

# ---------------------------------------------------------------------------- validate failure
setup
before="$(sha "$T/state/campaign.json")"
export FAKE_VALIDATE_CODE=2
if ! run_apply > "$T/out" 2>&1; then ok "validate failure exits non-zero"; else no "validate failure exits non-zero"; fi
[ "$(sha "$T/state/campaign.json")" = "$before" ] && ok "validate failure leaves the save untouched" || no "validate failure leaves the save untouched"
started && ok "validate failure starts the container again" || no "validate failure starts the container again"
unset FAKE_VALIDATE_CODE
cleanup

# -------------------------------------------------------------------------------- status timeout
setup
before="$(sha "$T/state/campaign.json")"
printf '{"has_save":false}\n' > "$T/status.json"
if ! run_apply > "$T/out" 2>&1; then ok "unhealthy status exits non-zero"; else no "unhealthy status exits non-zero"; fi
[ "$(sha "$T/state/campaign.json")" = "$before" ] && ok "status timeout restores the backup" || no "status timeout restores the backup"
started && ok "status timeout starts the container again" || no "status timeout starts the container again"
cleanup

# --------------------------------------------------------------------------- wrong roster count
export FAKE_ADDED=5          # staging adds one row; the request claims five — set BEFORE setup,
                             # which bakes it into the env array
setup
before="$(sha "$T/state/campaign.json")"
if ! run_apply > "$T/out" 2>&1; then ok "wrong roster count exits non-zero"; else no "wrong roster count exits non-zero"; fi
[ "$(sha "$T/state/campaign.json")" = "$before" ] && ok "wrong roster count restores the backup" || no "wrong roster count restores the backup"
unset FAKE_ADDED
cleanup

# ------------------------------------------------------------------------------- killed midway
# The command runs directly (not through the function) so `$!` IS the apply process: backgrounding a
# shell FUNCTION gives `$!` the subshell, and killing that leaves the script orphaned and running.
setup
before="$(sha "$T/state/campaign.json")"
export FAKE_SLOW=2
env "${E[@]}" bash "$APPLY" "$T/work" > "$T/out" 2>&1 &
pid=$!
sleep 1
kill -TERM "$pid" 2>/dev/null
wait "$pid" 2>/dev/null
killed_code=$?
for _ in $(seq 1 20); do started && break; sleep 0.5; done
[ "${killed_code:-0}" != "0" ] && ok "killed midway exits non-zero" || no "killed midway exits non-zero (got ${killed_code:-0})"
[ "$(sha "$T/state/campaign.json")" = "$before" ] && ok "killed midway restores the save" || no "killed midway restores the save"
started && ok "killed midway starts the container again" || no "killed midway starts the container again"
grep -q "ROLLBACK" "$T/out" && ok "killed midway logs a rollback" || no "killed midway logs a rollback"
unset FAKE_SLOW
cleanup

# ---------------------------------------------------------------------------------------- lock
setup
(
    exec 9>"$T/admin.lock"
    flock -n 9 || exit 1
    sleep 3
) &
holder=$!
sleep 0.5
run_apply > "$T/out" 2>&1
code=$?
wait "$holder" 2>/dev/null
[ "$code" = "99" ] && ok "a second apply exits 99" || no "a second apply exits 99 (got $code)"
[ ! -s "$CALLS" ] && ok "the locked-out apply never touches docker" || no "the locked-out apply never touches docker"
cleanup

# ---------------------------------------------------------------------- a handle with a space
setup
python3 - "$T/state/campaign.json" "$T/staging.json" <<'PY'
import json, sys
for path in sys.argv[1:]:
    data = json.load(open(path))
    data["identity"]["handle_name"] = "Rain & Fina"
    json.dump(data, open(path, "w"))
PY
printf '{"has_save":true,"name":"Rain & Fina","rank":3}\n' > "$T/status.json"
if run_apply > "$T/out" 2>&1; then ok "a handle with a space still applies"; else no "a handle with a space still applies ($(tail -2 "$T/out"))"; fi
cleanup

# ------------------------------------------------------------------------------------- summary
echo "----"
echo "$PASS passed, $FAIL failed"
[ "$FAIL" = "0" ]
