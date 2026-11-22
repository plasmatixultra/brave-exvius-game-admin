import json
import os
import subprocess
import sys
import tempfile
import threading
import time

import config
import engine
import transport

ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE = engine.cache_root()
SAVE = os.path.join(CACHE, "save.json")
AGENT = os.path.join(ROOT, "agent", "agent.py")

ENGINE_MAX_AGE = 24 * 3600    # the image's source only changes on a pull

MUTATING_OPS = ("apply",)


_SETTINGS = [None]


def _settings():
    """The resolved :class:`config.Settings`, loaded once per process."""
    if _SETTINGS[0] is None:
        _SETTINGS[0] = config.load()
    return _SETTINGS[0]


def _server_dir():
    """The engine source, exported from the image (cached by image id)."""
    return os.path.join(CACHE, "engine")


def server_path(*parts):
    """A file inside the game's server directory, on the host."""
    return os.path.join(_settings().server_dir, *parts)


def _data_dir():
    return os.path.join(_settings().data_dir, "static")


def _stamp_path():
    return os.path.join(CACHE, ".stamp.json")


def _stamp():
    try:
        with open(_stamp_path(), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _write_stamp(values):
    os.makedirs(CACHE, exist_ok=True)
    with open(_stamp_path(), "w", encoding="utf-8") as fh:
        json.dump(values, fh)


def cache_ready():
    """True when the engine and the tables are readable. False, not an exception,
    when the game cannot be found: /api/health has to be able to say so."""
    try:
        return (os.path.isdir(os.path.join(_data_dir(), "mst_decoded"))
                and os.path.isdir(os.path.join(_data_dir(), "text_decoded"))
                and os.path.isfile(os.path.join(_server_dir(), "player_state.py")))
    except config.SettingsError:
        return False


def sync_data(force=False, log=print):
    """Export the engine's source from the image. Idempotent and cheap once done."""
    stamp = _stamp()
    age = time.time() - float(stamp.get("data_at") or 0)
    if cache_ready() and not force and age < ENGINE_MAX_AGE:
        return False
    os.makedirs(CACHE, exist_ok=True)
    log("localdata: exporting the engine source from the image (one time)…")
    engine.ensure(_settings(), CACHE)
    if not cache_ready():
        raise RuntimeError(
            "the game's data directory is missing tables: %s" % _data_dir())
    stamp["data_at"] = time.time()
    _write_stamp(stamp)
    log("localdata: cache ready at " + CACHE)
    return True


def _signature(path):
    try:
        info = os.stat(path)
    except OSError:
        return None
    return (info.st_mtime_ns, info.st_size)


def _save_is_fresh():
    """Fresh means: the cache exists and the live file is the one we copied."""
    if not os.path.isfile(SAVE):
        return False
    stamp = _stamp()
    recorded = stamp.get("save_source_signature")
    return bool(recorded) and str(_signature(_settings().save)) == recorded


def _read_save_bytes():
    """The live save's bytes. Falls back to a container read: the game may
    write it as root with no world-read permission."""
    path = _settings().save
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except PermissionError:
        return transport.run_in_image(
            _settings().image,
            [(_settings().server_dir, "/srv/ffbe/server-root", "ro")],
            ["/srv/ffbe/server-root/state/campaign.json"],
            timeout=120, entrypoint="cat").stdout.encode()


# The page is a ThreadingHTTPServer: on a stale save several requests arrive at once. One pull at a
# time, or concurrent writers move the same temp file out from under each other.
_SAVE_LOCK = threading.Lock()


def refresh_save(force=False):
    """Mirror the live save into the cache. Returns True when a read happened."""
    if not force and _save_is_fresh():
        return False
    live = str(_signature(_settings().save))
    with _SAVE_LOCK:
        if not force and _save_is_fresh():     # another thread may have just done it
            return False
        os.makedirs(CACHE, exist_ok=True)
        handle, tmp = tempfile.mkstemp(dir=CACHE, prefix="save.", suffix=".json")
        try:
            with os.fdopen(handle, "wb") as fh:
                fh.write(_read_save_bytes())
            os.replace(tmp, SAVE)      # a half-written pull can never be read
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        stamp = _stamp()
        stamp["save_at"] = time.time()
        stamp["save_source_signature"] = live
        stamp["save_signature"] = str(_signature(SAVE))
        _write_stamp(stamp)
        return True


def env():
    return {
        **os.environ,
        "PYTHONPATH": _server_dir(),
        "MST_DIR": os.path.join(_data_dir(), "mst_decoded"),
        "TEXT_DIR": os.path.join(_data_dir(), "text_decoded"),
        "FFBE_DATA": os.path.join(CACHE, "work"),
    }


_AGENT_LOCK = threading.Lock()   # two previews in flight must not share the work dir


def agent(op, edits=None, flags=None):
    """Run one READ-ONLY agent op locally against the cached save; returns its `result`."""
    if op in MUTATING_OPS:
        raise RuntimeError(f"{op} is not a read op; applies go through pipeline.apply")
    with _AGENT_LOCK:
        return _agent_locked(op, edits, flags)


def _agent_locked(op, edits, flags):
    sync_data()
    refresh_save()
    argv = [sys.executable, AGENT, op, SAVE]
    edits_path = None
    if edits is not None:
        # One file per call: two previews can be in flight at once (the side pane's debounce and the
        # cart's click), and a shared `edits.json` let one process read the other's payload.
        handle, edits_path = tempfile.mkstemp(prefix="edits-", suffix=".json", dir=CACHE)
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(edits, fh)
        argv.append(edits_path)
    for name, value in (flags or {}).items():
        argv.extend([f"--{name}", str(value)])
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, env=env(), timeout=120)
    finally:
        if edits_path and os.path.exists(edits_path):
            os.unlink(edits_path)
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
