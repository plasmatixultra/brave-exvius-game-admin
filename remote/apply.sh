#!/usr/bin/env bash
# Athena-side apply. One run, one lock, one trap.
#
#   bash apply.sh <workdir>
#
# <workdir> must already hold: agent/ (the agent source), edits.json.
# This script stops the container FIRST, then backs up, then edits a COPY of the save, validates
# that copy against the backup, replaces the live file with an atomic `mv` of a same-filesystem
# temp file, starts the container and polls it until it reports the new save.
#
# Any failure after the stop — including this script being killed, or the local app dying (the trap
# runs on athena, not on your laptop) — restores the backup and starts the container again.
#
# Exit codes: 0 ok · 2 refused/mismatch · 3 status timeout · 99 another apply holds the lock.
set -uo pipefail

SAVE="${FFBE_SAVE:-$HOME/exvius/server/state/campaign.json}"
BACKUP_DIR="${FFBE_BACKUP_DIR:-$HOME/exvius/server/state/backups}"
LOCK="${FFBE_LOCK:-$HOME/exvius/.admin.lock}"
CONTAINER="${FFBE_CONTAINER:-ffbe}"
IMAGE="${FFBE_IMAGE:-ffbe-ios-server:local}"
DATA_DIR="${FFBE_DATA_DIR:-$HOME/exvius/data}"
STATUS_TIMEOUT="${FFBE_STATUS_TIMEOUT:-60}"

WORKDIR="${1:?usage: apply.sh <workdir>}"
SAVE_DIR="$(dirname "$SAVE")"
STAGING="$WORKDIR/staging.json"
BACKUP=""
STOPPED=0
DONE=0
ROLLED_BACK=0

log() { echo "$*"; }
fail() { echo "ERROR: $*" >&2; exit "${2:-1}"; }

restore() {
    local status=$?
    # TERM runs this, then `exit` fires the EXIT trap again: roll back once, not twice.
    if [ "$ROLLED_BACK" = "1" ]; then
        exit "$status"
    fi
    if [ "$STOPPED" = "1" ] && [ "$DONE" != "1" ]; then
        ROLLED_BACK=1
        # A rollback is NEVER a success. A TERM that lands while the agent is running leaves the
        # trap with the child's exit code (0), which would otherwise read as "applied".
        [ "$status" -eq 0 ] && status=1
        echo "ROLLBACK: restoring the pre-apply save" >&2
        if [ -n "$BACKUP" ] && [ -f "$BACKUP" ]; then
            sudo -n cp -p "$BACKUP" "$SAVE_DIR/campaign.json.tmp" \
                && sudo -n mv "$SAVE_DIR/campaign.json.tmp" "$SAVE" \
                || echo "ROLLBACK FAILED: the backup is at $BACKUP" >&2
        else
            echo "ROLLBACK: no backup was taken; the live save is untouched" >&2
        fi
        docker start "$CONTAINER" >/dev/null 2>&1 || true
    fi
    exit "$status"
}
trap restore EXIT INT TERM HUP

mkdir -p "$WORKDIR" || fail "cannot create $WORKDIR"

# --- 1. one apply at a time --------------------------------------------------------------------
exec 9>"$LOCK" || fail "cannot open the lock file $LOCK"
if ! flock -n 9; then
    echo "apply already running" >&2
    exit 99
fi

# --- 2. stop -----------------------------------------------------------------------------------
log "stopping $CONTAINER"
docker stop "$CONTAINER" >/dev/null || fail "docker stop $CONTAINER failed"
STOPPED=1

# --- 3. timestamped backup, after the stop so no request lands between the two -----------------
sudo -n mkdir -p "$BACKUP_DIR" || fail "cannot create $BACKUP_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="$BACKUP_DIR/campaign-$STAMP.json"
sudo -n cp -p "$SAVE" "$BACKUP" || fail "backup failed; the live save is untouched" 1
log "backup $BACKUP"
cp "$BACKUP" "$WORKDIR/baseline.json" || fail "cannot read the backup back"

# Pre-apply identity, from the file the container just flushed.
read -r PRE_NAME PRE_RANK PRE_ROSTER < <(python3 -I - "$SAVE" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print(d["identity"]["handle_name"], int(d["rank"]["level"]), len(d["roster"]))
PY
) || fail "cannot read the save's identity"
[ -n "${PRE_NAME:-}" ] || fail "cannot read the save's identity"

cp "$SAVE" "$WORKDIR/save.json" || fail "cannot copy the save"

agent() {
    docker run --rm --entrypoint python3 \
        -e PYTHONPATH=/app/server \
        -e MST_DIR=/srv/ffbe/data/static/mst_decoded \
        -e TEXT_DIR=/srv/ffbe/data/static/text_decoded \
        -e FFBE_DATA=/work/scratch \
        -v "$DATA_DIR":/srv/ffbe/data:ro \
        -v "$WORKDIR":/work \
        "$IMAGE" /work/agent/agent.py "$@"
}

# --- 4. edit the COPY --------------------------------------------------------------------------
log "applying edits"
agent apply /work/save.json /work/edits.json --out /work/staging.json --workdir /work \
    || fail "the agent refused the edits (nothing was written)" 2

# --- 5. validate the copy against the backup ---------------------------------------------------
log "validating"
agent validate /work/staging.json --baseline /work/baseline.json --expected /work/expected.json \
    || fail "the staged save failed validation (nothing was written)" 2

# --- 6. atomic replace: same directory, same filesystem, mv only -------------------------------
sudo -n cp "$STAGING" "$SAVE_DIR/campaign.json.tmp" || fail "cannot stage the new save"
sudo -n mv "$SAVE_DIR/campaign.json.tmp" "$SAVE" || fail "cannot replace the save"
log "save replaced"

# --- 7. start ----------------------------------------------------------------------------------
log "starting $CONTAINER"
docker start "$CONTAINER" >/dev/null || fail "docker start $CONTAINER failed"

# --- 8. poll until the server reports the new save --------------------------------------------
ADDED=0
if [ -f "$WORKDIR/expected.json" ]; then
    ADDED="$(python3 -I -c 'import json,sys; print(int(json.load(open(sys.argv[1])).get("roster_added", 0)))' "$WORKDIR/expected.json")" \
        || fail "cannot read expected.json"
fi
DEADLINE=$((SECONDS + STATUS_TIMEOUT))
# `-c`, not `-`: stdin carries the status JSON, so the program cannot come from stdin too.
STATUS_CHECK='import json,sys
status = json.load(sys.stdin)
save = json.load(open(sys.argv[1]))
ok = (status.get("has_save") is True
      and str(status.get("name")) == sys.argv[2]
      and int(status.get("rank", -1)) == int(sys.argv[3])
      and len(save.get("roster") or []) == int(sys.argv[4]) + int(sys.argv[5]))
sys.exit(0 if ok else 1)'
while :; do
    if docker exec "$CONTAINER" python3 /app/ffbe.py status 2>/dev/null \
            | python3 -I -c "$STATUS_CHECK" "$STAGING" "$PRE_NAME" "$PRE_RANK" "$PRE_ROSTER" "$ADDED"; then
        log "status ok: $PRE_NAME rank $PRE_RANK, roster $((PRE_ROSTER + ADDED))"
        break
    fi
    if [ "$SECONDS" -ge "$DEADLINE" ]; then
        fail "the server did not come back healthy within ${STATUS_TIMEOUT}s" 3
    fi
    sleep 1
done

DONE=1
log "apply complete"
exit 0
