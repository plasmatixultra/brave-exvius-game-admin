#!/bin/sh
# Run the agent test suite INSIDE the server image on athena.
#
# The agent edits saves with the server's own `player_state`, which only exists inside the
# image, so the tests cannot run locally: this syncs the tree the tests need, then runs
# `python3 -m unittest` in a throwaway container with the repo mounted read-only and a
# writable scratch dir.
#
#   tests/run_in_container.sh                       # discover every agent test
#   tests/run_in_container.sh tests.agent.test_basic -v
#
# Nothing here touches the live save or the live container.
set -eu

REMOTE="${FFBE_REMOTE:-plasmatixultra@10.129.1.139}"
IMAGE="ffbe-ios-server:local"
REMOTE_ROOT="exvius-admin-tests"

LOCAL_REPO="$(cd "$(dirname "$0")/.." && pwd)"

ssh -o BatchMode=yes "$REMOTE" \
  "mkdir -p ~/$REMOTE_ROOT/repo ~/$REMOTE_ROOT/scratch"

# --delete keeps the remote copy honest: a stale module cannot be imported by accident.
for dir in agent tests data; do
  [ -d "$LOCAL_REPO/$dir" ] || continue
  rsync -a --delete --exclude '__pycache__' \
    "$LOCAL_REPO/$dir/" "$REMOTE:$REMOTE_ROOT/repo/$dir/"
done

if [ "$#" -eq 0 ]; then
  set -- discover -s tests/agent -t . -v
fi

# $HOME and the container args are expanded on athena, hence the escaped sigil.
ssh -o BatchMode=yes "$REMOTE" "docker run --rm \
  -e PYTHONPATH=/app/server \
  -e MST_DIR=/srv/ffbe/data/static/mst_decoded \
  -e TEXT_DIR=/srv/ffbe/data/static/text_decoded \
  -e FFBE_DATA=/scratch/ffbe_data \
  -e TEST_SCRATCH=/scratch \
  -v \$HOME/$REMOTE_ROOT/repo:/repo:ro \
  -v \$HOME/$REMOTE_ROOT/scratch:/scratch \
  -v \$HOME/exvius/data:/srv/ffbe/data:ro \
  -w /repo \
  --entrypoint python3 $IMAGE -m unittest $*"
