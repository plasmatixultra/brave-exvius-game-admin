#!/bin/sh
# Run the agent test suite INSIDE the server image, on this machine.
#
# The agent edits saves with the server's own `player_state`, which only exists inside the
# image, so these tests cannot run on the host: this starts a throwaway container with the
# repo mounted read-only, a writable scratch dir and the game's data mounted read-only.
#
#   tests/run_in_container.sh                             # discover every agent test
#   tests/run_in_container.sh tests.agent.test_basic -v
#
# The data directory comes from the running container's own mount (docker inspect), so this
# works on any machine running the game without being told where it lives.
# Nothing here touches the live save or the live container.
set -eu

IMAGE="${FFBE_IMAGE:-ffbe-ios-server:local}"
CONTAINER="${FFBE_CONTAINER:-ffbe}"

LOCAL_REPO="$(cd "$(dirname "$0")/.." && pwd)"
DATA_DIR="${FFBE_DATA_DIR:-$(docker inspect "$CONTAINER" \
  --format '{{range .Mounts}}{{if eq .Destination "/srv/ffbe/data"}}{{.Source}}{{end}}{{end}}')}"

if [ -z "$DATA_DIR" ]; then
  echo "run_in_container: cannot find the game's data directory;" >&2
  echo "  is the $CONTAINER container present, or FFBE_DATA_DIR set?" >&2
  exit 1
fi

SCRATCH="$(mktemp -d)"
cleanup() { find "$SCRATCH" -mindepth 1 -delete 2>/dev/null || true; rmdir "$SCRATCH" 2>/dev/null || true; }
trap cleanup EXIT INT TERM HUP

if [ "$#" -eq 0 ]; then
  set -- discover -s tests/agent -t . -v
fi

exec docker run --rm -i \
  -e PYTHONPATH=/app/server \
  -e MST_DIR=/srv/ffbe/data/static/mst_decoded \
  -e TEXT_DIR=/srv/ffbe/data/static/text_decoded \
  -e FFBE_DATA=/scratch/ffbe_data \
  -e TEST_SCRATCH=/scratch \
  -v "$LOCAL_REPO:/repo:ro" \
  -v "$SCRATCH:/scratch" \
  -v "$DATA_DIR:/srv/ffbe/data:ro" \
  -w /repo \
  --entrypoint python3 "$IMAGE" -m unittest "$@"
