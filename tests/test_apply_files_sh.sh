#!/usr/bin/env bash
# Exercise runner/apply-files.sh against a scratch tree and a stub agent.
# No docker, no container, no real save: this script is the part that runs INSIDE the image.
#
#   tests/test_apply_files_sh.sh    → one line per case, non-zero exit if any case fails
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
FILES="$HERE/../runner/apply-files.sh"
PASS=0
FAIL=0

ok() { echo "ok   - $1"; PASS=$((PASS + 1)); }
no() { echo "FAIL - $1"; FAIL=$((FAIL + 1)); }

setup() {
    T="$(mktemp -d)"
    mkdir -p "$T/server/state/backups" "$T/work" "$T/agent/agent"
    cat > "$T/server/state/campaign.json" <<'JSON'
{"identity":{"user_id":"1","handle_name":"Rain"},"rank":{"level":3},"roster":[{"id":1}],"currencies":{"gil":5}}
JSON
    cat > "$T/work/edits.json" <<'JSON'
{"currencies":{"gil":1}}
JSON
    cp "$T/server/state/campaign.json" "$T/work/save.json"

    # Stub agent: `apply` writes a staged save and an expectation; `validate` succeeds unless told not to.
    cat > "$T/agent/agent/agent.py" <<'PY'
import json, os, sys
verb = sys.argv[1]
if verb == "apply":
    if os.environ.get("STUB_REFUSE"):
        print('{"ok": false, "error": "edits do not match the request"}')
        sys.exit(2)
    out = sys.argv[sys.argv.index("--out") + 1]
    work = sys.argv[sys.argv.index("--workdir") + 1]
    save = json.load(open(sys.argv[2]))
    save["currencies"]["gil"] += 1
    save["roster"].append({"id": 2})
    json.dump(save, open(out, "w"))
    json.dump({"currencies": {"gil": 1}, "roster_added": 1}, open(os.path.join(work, "expected.json"), "w"))
    print('{"ok": true}')
elif verb == "validate":
    if os.environ.get("STUB_INVALID"):
        print("validate: roster_added mismatch")
        sys.exit(2)
    print('{"ok": true}')
else:
    sys.exit(3)
PY
}

run() { ( cd "$T" && PATH="$T/bin:$PATH" AGENT_DIR="$T/agent" bash "$FILES" "$@" ); }

trap 'find "$T" -mindepth 1 -delete 2>/dev/null; rmdir "$T" 2>/dev/null' EXIT

# --- backup ------------------------------------------------------------------------------------
setup
out="$(run backup "$T/server" "$T/work")"; code=$?
[ "$code" -eq 0 ] && ok "backup exits 0" || no "backup exits 0 (got $code)"
printf '%s' "$out" | grep -q '^apply-files: backup ' && ok "backup prints the backup path" || no "backup prints the backup path: $out"
count="$(ls "$T/server/state/backups" | grep -c '^campaign-.*\.json$')"
[ "$count" = "1" ] && ok "backup creates one timestamped copy" || no "backup creates one timestamped copy (got $count)"
cmp -s "$T/server/state/campaign.json" "$T/server/state/backups/"campaign-*.json \
    && ok "the backup is byte-identical to the save" || no "the backup is byte-identical to the save"
[ -f "$T/work/baseline.json" ] && ok "backup leaves a baseline in the workdir" || no "backup leaves a baseline in the workdir"
[ -f "$T/work/backup" ] && ok "backup records its path for restore" || no "backup records its path for restore"
grep -q '"name": "Rain"' "$T/work/identity.json" \
    && ok "backup records the identity the status poll needs" || no "backup records the identity"
setup

# --- apply -------------------------------------------------------------------------------------
run backup "$T/server" "$T/work" > /dev/null
run apply "$T/server" "$T/work" > /dev/null; code=$?
[ "$code" -eq 0 ] && ok "apply exits 0" || no "apply exits 0 (got $code)"
[ -f "$T/work/staging.json" ] && ok "apply writes staging.json" || no "apply writes staging.json"
[ -f "$T/work/expected.json" ] && ok "apply writes expected.json" || no "apply writes expected.json"
cmp -s "$T/server/state/campaign.json" "$T/work/save.json" \
    && ok "apply does not touch the live save" || no "apply does not touch the live save"
out="$(STUB_REFUSE=1 run apply "$T/server" "$T/work" 2>/dev/null)"; code=$?
[ "$code" -eq 2 ] && ok "a refusal exits 2" || no "a refusal exits 2 (got $code)"
[ -z "$out" ] && ok "a refusal prints nothing as if it worked" || no "a refusal prints nothing as if it worked: $out"
[ -f "$T/server/state/campaign.json" ] && ok "a refusal leaves the save alone" || no "a refusal leaves the save alone"
setup

# --- validate ----------------------------------------------------------------------------------
run backup "$T/server" "$T/work" > /dev/null
run apply "$T/server" "$T/work" > /dev/null
run validate "$T/server" "$T/work" > /dev/null; code=$?
[ "$code" -eq 0 ] && ok "validate exits 0 on a matching stage" || no "validate exits 0 (got $code)"
STUB_INVALID=1 run validate "$T/server" "$T/work" > /dev/null 2>&1; code=$?
[ "$code" -eq 2 ] && ok "validate exits 2 on a mismatch" || no "validate exits 2 (got $code)"
setup

# --- write -------------------------------------------------------------------------------------
before="$(md5sum < "$T/server/state/campaign.json")"
run backup "$T/server" "$T/work" > /dev/null
run apply "$T/server" "$T/work" > /dev/null
run write "$T/server" "$T/work" > /dev/null; code=$?
[ "$code" -eq 0 ] && ok "write exits 0" || no "write exits 0 (got $code)"
cmp -s "$T/server/state/campaign.json" "$T/work/staging.json" \
    && ok "write replaces the save with the staged copy" || no "write replaces the save with the staged copy"
[ ! -e "$T/server/state/campaign.json.tmp" ] && ok "write leaves no temp file" || no "write leaves no temp file"
after="$(md5sum < "$T/server/state/campaign.json")"
[ "$before" != "$after" ] && ok "the save really changed" || no "the save really changed"

# --- restore -----------------------------------------------------------------------------------
run restore "$T/server" "$T/work" > /dev/null; code=$?
[ "$code" -eq 0 ] && ok "restore exits 0" || no "restore exits 0 (got $code)"
[ "$(md5sum < "$T/server/state/campaign.json")" = "$before" ] \
    && ok "restore puts the original bytes back" || no "restore puts the original bytes back"
[ ! -e "$T/server/state/campaign.json.tmp" ] && ok "restore leaves no temp file" || no "restore leaves no temp file"

# --- refusals ----------------------------------------------------------------------------------
run backup "$T/server" "$T/work" > /dev/null
printf 'restore: %s\n' "$T/server/state/backups/absent.json" > "$T/work/backup"
out="$(run restore "$T/server" "$T/work" 2>&1)"; code=$?
[ "$code" -eq 1 ] && ok "restore without a backup file exits 1" || no "restore without a backup exits 1 (got $code)"
printf '%s' "$out" | grep -q 'absent.json' && ok "restore names the backup it could not find" || no "restore names the missing backup"
out="$(run bogus "$T/server" "$T/work" 2>&1)"; code=$?
[ "$code" -eq 1 ] && ok "an unknown verb exits 1" || no "an unknown verb exits 1 (got $code)"

echo "---- $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
