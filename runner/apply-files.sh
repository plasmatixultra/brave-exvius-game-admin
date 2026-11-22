#!/usr/bin/env bash
# File worker. Runs INSIDE the game image, as whatever user the image runs as
# (root), with the game's server directory and a work directory mounted in.
#
#   apply-files.sh <verb> <server_root> <workdir> [backup_path]
#
# Verbs, in the order the host orchestrator calls them:
#   backup    copy the live save to <server_root>/state/backups/campaign-<UTC>.json,
#             leave baseline.json + save.json + backup + identity.json in <workdir>
#   apply     run the agent to write <workdir>/staging.json from save.json + edits.json
#   validate  check staging.json against baseline.json and expected.json
#   write     atomically replace the live save with staging.json
#   restore   put the backup named in <workdir>/backup back in place
#
# Every mutation of the live save happens here, in the container, so ownership
# is the image's business and the host never needs root.
#
# Exit codes: 0 ok · 2 refused/failed validation (nothing written) · 1 anything else.
set -uo pipefail

AGENT_DIR="${AGENT_DIR:-/admin}"
SAVE_REL="state/campaign.json"
BACKUP_REL="state/backups"

usage() {
    echo "usage: apply-files.sh backup|apply|validate|write|restore <server_root> <workdir>" >&2
    exit 1
}

say() { echo "apply-files: $*"; }
die() { echo "apply-files: $*" >&2; exit "${2:-1}"; }

VERB="${1:-}"
SERVER="${2:-}"
WORKDIR="${3:-}"
BACKUP_ARG="${4:-}"
[ -n "$VERB" ] && [ -n "$SERVER" ] && [ -n "$WORKDIR" ] || usage

SAVE="$SERVER/$SAVE_REL"
STAGING="$WORKDIR/staging.json"

agent() {
    python3 "$AGENT_DIR/agent/agent.py" "$@"
}

do_backup() {
    [ -f "$SAVE" ] || die "no save at $SAVE"
    mkdir -p "$SERVER/$BACKUP_REL" || die "cannot create $SERVER/$BACKUP_REL"
    local stamp backup
    stamp="$(date -u +%Y%m%dT%H%M%SZ)-$$"     # seconds collide: two applies, one name
    backup="$SERVER/$BACKUP_REL/campaign-$stamp.json"
    cp -p "$SAVE" "$backup" || die "backup failed; the live save is untouched"
    cp -p "$backup" "$WORKDIR/baseline.json" || die "cannot read the backup back"
    cp -p "$SAVE" "$WORKDIR/save.json" || die "cannot copy the save"
    # The host never reads the save itself (it may be root-owned); ask the
    # container for the identity the status poll has to match.
    python3 - "$WORKDIR/save.json" "$WORKDIR/identity.json" <<'PY' || die "cannot read the save's identity"
import json, sys
save = json.load(open(sys.argv[1]))
json.dump({"name": str(save["identity"]["handle_name"]),
           "rank": int(save["rank"]["level"]),
           "roster": len(save["roster"])}, open(sys.argv[2], "w"))
PY
    printf '%s\n' "$backup" > "$WORKDIR/backup" || die "cannot record the backup path"
    say "backup $backup"
}

do_apply() {
    [ -f "$WORKDIR/save.json" ] || die "no save.json in $WORKDIR"
    [ -f "$WORKDIR/edits.json" ] || die "no edits.json in $WORKDIR"
    local output
    output="$(agent apply "$WORKDIR/save.json" "$WORKDIR/edits.json" \
        --out "$STAGING" --workdir "$WORKDIR" 2>&1)" || {
        echo "$output" >&2
        echo "apply-files: the agent refused the edits (nothing was written)" >&2
        exit 2
    }
    [ -f "$STAGING" ] || die "the agent reported success but wrote no staging.json"
    say "staged $(wc -c < "$STAGING") bytes"
}

do_validate() {
    [ -f "$STAGING" ] || die "no staging.json to validate"
    local output
    output="$(agent validate "$STAGING" --baseline "$WORKDIR/baseline.json" \
        --expected "$WORKDIR/expected.json" 2>&1)" || {
        echo "$output" >&2
        echo "apply-files: the staged save failed validation (nothing was written)" >&2
        exit 2
    }
    say "validated"
}

replace() {
    # Same directory, same filesystem: mv is the atomic step, and a reader either
    # sees the old file or the new one, never a half-written one.
    local source="$1" tmp="$SERVER/state/campaign.json.tmp"
    cp -p "$source" "$tmp" || die "cannot stage the replacement"
    mv "$tmp" "$SAVE" || die "cannot replace $SAVE"
}

do_write() {
    [ -f "$STAGING" ] || die "no staging.json to write"
    replace "$STAGING"
    say "save replaced"
}

do_restore() {
    local backup="$BACKUP_ARG"
    if [ -z "$backup" ]; then
        # Accept the bare path our own backup wrote, and the `restore: <path>` form
        # earlier workdirs used.
        backup="$(sed -n '1{s/^restore: //;p}' "$WORKDIR/backup" 2>/dev/null)"
    fi
    [ -n "$backup" ] || die "no backup recorded in $WORKDIR/backup"
    [ -f "$backup" ] || die "the recorded backup does not exist: $backup"
    replace "$backup"
    say "save restored from $backup"
}

case "$VERB" in
    backup)   do_backup ;;
    apply)    do_apply ;;
    validate) do_validate ;;
    write)    do_write ;;
    restore)  do_restore ;;
    *)        echo "apply-files: unknown verb: $VERB" >&2; usage ;;
esac
