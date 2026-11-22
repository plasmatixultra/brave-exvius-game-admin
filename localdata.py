"""Read-side cache: the save and the master data live on THIS machine.

Why: every `docker run` costs ~1.8 s (container create + load), and a query used to need four ssh
round trips on top. But the agent's read ops only need two things — the save and the shipped tables —
and the server's own `player_state` runs correctly on the local interpreter (verified 2026-11-21:
`unit_stats("213000105", 5)` = [1011, 44, 40, 35, 34, 34] on host Python 3.14.10 and inside the
3.12.16 image, identical; `state_coherence_issues` empty on the live save). So the page reads from a
cached copy and Docker is touched only when applying.

    scratch/cache/server/            ~/exvius/repo/source/server       (the server code, 81 MB)
    scratch/cache/data/mst_decoded/  ~/exvius/data/static/mst_decoded  (165 MB)
    scratch/cache/data/text_decoded/ ~/exvius/data/static/text_decoded (91 MB)
    scratch/cache/save.json          ~/exvius/server/state/campaign.json (pulled on a short TTL)

`scratch/` is git-ignored. First use pays a one-time ~330 MB rsync; after that the only network
traffic is the save pull (one `cp` over the multiplexed ssh connection).
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time

import remote

ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(ROOT, "scratch", "cache")
SERVER = os.path.join(CACHE, "server")
DATA = os.path.join(CACHE, "data")
SAVE = os.path.join(CACHE, "save.json")
STAMP = os.path.join(CACHE, ".stamp.json")
AGENT = os.path.join(ROOT, "agent", "agent.py")

DATA_MAX_AGE = 24 * 3600      # the shipped tables only change on a server update
SAVE_MAX_AGE = 20             # seconds; the save changes as you play

MUTATING_OPS = ("apply",)


def _stamp():
    try:
        with open(STAMP, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _write_stamp(values):
    os.makedirs(CACHE, exist_ok=True)
    with open(STAMP, "w", encoding="utf-8") as fh:
        json.dump(values, fh)


def cache_ready():
    return (os.path.isdir(os.path.join(DATA, "mst_decoded"))
            and os.path.isdir(os.path.join(DATA, "text_decoded"))
            and os.path.isfile(os.path.join(SERVER, "player_state.py")))


def sync_data(force=False, log=print):
    """Mirror the server code and the shipped tables. Idempotent and cheap once done."""
    stamp = _stamp()
    age = time.time() - float(stamp.get("data_at") or 0)
    if cache_ready() and not force and age < DATA_MAX_AGE:
        return False
    os.makedirs(CACHE, exist_ok=True)
    log("localdata: syncing the server code and master data (~330 MB, one time)…")
    remote.put_tree_dirs([
        ("exvius/repo/source/server/", os.path.join(CACHE, "server")),
        ("exvius/data/static/mst_decoded/", os.path.join(DATA, "mst_decoded")),
        ("exvius/data/static/text_decoded/", os.path.join(DATA, "text_decoded")),
    ])
    stamp["data_at"] = time.time()
    _write_stamp(stamp)
    log("localdata: cache ready at " + CACHE)
    return True


def _save_is_fresh():
    stamp = _stamp()
    age = time.time() - float(stamp.get("save_at") or 0)
    return os.path.isfile(SAVE) and age < SAVE_MAX_AGE


# The page is a ThreadingHTTPServer: on a stale save several requests arrive at once. One pull at a
# time, or concurrent writers move the same temp file out from under each other.
_SAVE_LOCK = threading.Lock()


def refresh_save(force=False):
    """Pull the live save into the cache. Returns True when a pull happened."""
    if not force and _save_is_fresh():
        return False
    with _SAVE_LOCK:
        if not force and _save_is_fresh():     # another thread may have just done it
            return False
        os.makedirs(CACHE, exist_ok=True)
        handle, tmp = tempfile.mkstemp(dir=CACHE, prefix="save.", suffix=".json")
        try:
            with os.fdopen(handle, "wb") as fh:
                fh.write(remote.get_bytes("exvius/server/state/campaign.json"))
            os.replace(tmp, SAVE)      # a half-written pull can never be read
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        stamp = _stamp()
        stamp["save_at"] = time.time()
        _write_stamp(stamp)
        return True


def env():
    return {
        **os.environ,
        "PYTHONPATH": SERVER,
        "MST_DIR": os.path.join(DATA, "mst_decoded"),
        "TEXT_DIR": os.path.join(DATA, "text_decoded"),
        "FFBE_DATA": os.path.join(CACHE, "work"),
    }


def agent(op, edits=None, flags=None):
    """Run one READ-ONLY agent op locally against the cached save; returns its `result`."""
    if op in MUTATING_OPS:
        raise RuntimeError(f"{op} is not a read op; applies go through pipeline.apply")
    sync_data()
    refresh_save()
    argv = [sys.executable, AGENT, op, SAVE]
    if edits is not None:
        edits_path = os.path.join(CACHE, "edits.json")
        with open(edits_path, "w", encoding="utf-8") as fh:
            json.dump(edits, fh)
        argv.append(edits_path)
    for name, value in (flags or {}).items():
        argv.extend([f"--{name}", str(value)])
    proc = subprocess.run(argv, capture_output=True, text=True, env=env(), timeout=120)
    try:
        payload = json.loads(proc.stdout)
    except ValueError:
        raise RuntimeError(f"local agent printed no JSON: {proc.stdout[:200]!r} "
                           f"{proc.stderr[:200]!r}") from None
    if not payload.get("ok"):
        raise RuntimeError(payload.get("error") or "the agent refused")
    return payload["result"]


if __name__ == "__main__":   # `python3 localdata.py` primes the cache and prints timings
    started = time.time()
    sync_data(force="--force" in sys.argv)
    refresh_save(force=True)
    print(f"cache: {cache_ready()} · sync+pull {time.time() - started:.2f}s")
    for op in ("summary", "catalog"):
        t0 = time.time()
        result = agent(op)
        size = len(json.dumps(result))
        print(f"{op:10s} {time.time() - t0:5.2f}s · {size} bytes")
