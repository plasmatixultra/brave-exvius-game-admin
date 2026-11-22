"""The apply sequence: stop, back up, edit through a container, swap, start, poll.

This is the only code that can replace a save. It runs on the host but never
opens the save: every file operation is a `docker run` of
runner/apply-files.sh inside the game image, so ownership stays the image's
problem and the host needs no root.

A crash between the stop and the start leaves a marker naming the backup;
:func:`recover` puts that backup back and starts the container on the next run.
"""

import json
import os
import shutil
import tempfile
import threading
import time

import transport

SERVER_ROOT = "/srv/ffbe/server-root"
DATA_ROOT = "/srv/ffbe/data"
WORK_ROOT = "/work"
ADMIN_ROOT = "/admin"

#: The agent needs the image's own modules and the game's decoded tables.
AGENT_ENV = {
    "PYTHONPATH": "/app/server",
    "MST_DIR": "/srv/ffbe/data/static/mst_decoded",
    "TEXT_DIR": "/srv/ffbe/data/static/text_decoded",
    "FFBE_DATA": "/work/scratch",
}

EXIT_FAILED = 1
EXIT_REFUSED = 2
EXIT_STATUS_TIMEOUT = 3
EXIT_BUSY = 99

BACKUP_LINE = "apply-files: backup "


_IN_PROCESS = threading.Lock()

class ApplyBusy(RuntimeError):
    """Another apply holds the lock."""


class ApplyError(RuntimeError):
    def __init__(self, code, message, log=None):
        self.code = code
        self.log = list(log or [])
        super().__init__(message)


class Lock:
    """One apply at a time.

    The lock is a real OS file lock (``flock`` on POSIX, ``msvcrt.locking`` on
    Windows), so a crashed process releases it and the OS decides who holds it.
    The pid file inside is only there to say who is running in the message.
    Nothing here signals another process: ``os.kill(pid, 0)`` terminates the
    target on Windows, which would kill the apply it was asking about.
    """

    def __init__(self, path):
        self.path = path
        self.handle = None

    def __enter__(self):
        # The page is one process with one thread per request, and a file lock is
        # per open file description: two threads would both "hold" it. The
        # in-process mutex is what a second tab actually hits.
        if not _IN_PROCESS.acquire(blocking=False):
            raise ApplyBusy("another apply is already running")
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        handle = open(self.path, "a+")
        try:
            _try_lock(handle)
        except OSError:
            handle.close()
            _IN_PROCESS.release()
            raise ApplyBusy("another apply is already running")
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        self.handle = handle
        return self

    def __exit__(self, *exc):
        if self.handle is not None:
            try:
                _unlock(self.handle)
            finally:
                self.handle.close()
                self.handle = None
            _IN_PROCESS.release()
        return False


def _try_lock(handle):
    if os.name == "nt":                                  # pragma: no cover - Windows only
        import msvcrt
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(handle):
    if os.name == "nt":                                  # pragma: no cover - Windows only
        import msvcrt
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class ApplyError(RuntimeError):
    def __init__(self, code, message, log=None):
        self.code = code
        self.log = list(log or [])
        super().__init__(message)


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True


class Lock:
    """A lock file holding a pid. Portable: no fcntl, no flock."""

    def __init__(self, path):
        self.path = path
        self.held = False

    def __enter__(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                try:
                    with open(self.path, encoding="utf-8") as handle:
                        other = int(handle.read().strip() or 0)
                except (OSError, ValueError):
                    other = 0
                if other and _pid_alive(other):
                    raise ApplyBusy("another apply is already running (pid %s)" % other)
                try:
                    os.unlink(self.path)          # stale: its process is gone
                except FileNotFoundError:
                    pass
                continue
            with os.fdopen(fd, "w") as handle:
                handle.write(str(os.getpid()))
            self.held = True
            return self
        raise ApplyBusy("another apply is already running")

    def __exit__(self, *exc):
        if self.held:
            try:
                os.unlink(self.path)
            except FileNotFoundError:
                pass
            self.held = False


def _cache_dir():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "ffbe-admin")


def lock_path():
    return os.path.join(_cache_dir(), "apply.lock")


def marker_path():
    return os.path.join(_cache_dir(), "applying.json")


def _write_json(path, payload):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    os.replace(tmp, path)


def _write_marker(path, payload):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    _write_json(path, payload)


def _forget(marker, workdir, pid=None):
    """Drop the marker we wrote, and only that one."""
    if pid is None or _marker_pid(marker) == pid:
        try:
            os.unlink(marker)
        except FileNotFoundError:
            pass
    shutil.rmtree(workdir, ignore_errors=True)


def _marker_pid(marker):
    try:
        return _read_json(marker).get("pid")
    except (OSError, ValueError):
        return None


def _read_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _run_files(settings, workdir, verb, app_dir, extra=(), timeout=900):
    done = transport.run_in_image(
        settings.image_id or settings.image,
        [(settings.server_dir, SERVER_ROOT, "rw"),
         (settings.data_dir, DATA_ROOT, "ro"),
         (workdir, WORK_ROOT, "rw"),
         (app_dir, ADMIN_ROOT, "ro")],
        [ADMIN_ROOT + "/runner/apply-files.sh", verb, SERVER_ROOT, WORK_ROOT, *extra],
        timeout=timeout,
        env=AGENT_ENV,
        entrypoint="bash",
    )
    return done.stdout.strip()


def _step(settings, workdir, verb, app_dir, code):
    """Run one file verb, turning a non-zero exit into the right ApplyError."""
    try:
        return _run_files(settings, workdir, verb, app_dir)
    except transport.TransportError as error:
        detail = (error.stderr or "").strip().splitlines()
        raise ApplyError(code, detail[-1] if detail else str(error))


def _status_matches(line, identity):
    """The server's own report: same save, same name, same rank.

    Roster counts are not in it; the agent's validate step already proved the
    staged file against the backup and the request.
    """
    try:
        status = json.loads(line)
    except ValueError:
        return False
    if status.get("has_save") is not True or status.get("name") != identity.get("name"):
        return False
    try:
        return int(status.get("rank", -1)) == int(identity.get("rank", -2))
    except (TypeError, ValueError):
        return False                  # a half-loaded status has rank null


def _restore(settings, workdir, app_dir, backup):
    """Put the backup back and leave the container running.

    Restoring under a running server is pointless: it has the new save in memory
    and overwrites the file on its next autosave. So: stop, restore, start.
    Returns ``(restored, started, message)`` and never raises.
    """
    if not backup:
        started = True
        try:
            transport.start(settings.container)
        except transport.TransportError:
            started = False
        return (True, started,
                "ROLLBACK: no backup was taken; the live save is untouched")

    try:
        transport.stop(settings.container)
    except transport.TransportError:
        pass                     # already stopped, or it never started
    restored = True
    message = ""
    try:
        message = _run_files(settings, workdir, "restore", app_dir, extra=[backup])
    except (transport.TransportError, OSError) as error:
        restored = False
        message = "ROLLBACK FAILED: the backup is at %s (%s)" % (backup, error)
    started = True
    try:
        transport.start(settings.container)
    except transport.TransportError:
        started = False
        message += "; the container could not be started"
    return (restored, started, message)


def recover(settings, app_dir=None):
    """Undo an apply that died between its stop and its start. True if it did."""
    path = marker_path()
    if not os.path.exists(path):
        return False
    try:
        marker = _read_json(path)
    except (OSError, ValueError):
        marker = {}
    app_dir = app_dir or os.path.dirname(os.path.abspath(__file__))
    workdir = marker.get("workdir")
    if marker.get("backup") and workdir and os.path.isdir(workdir):
        restored, started, _message = _restore(settings, workdir, app_dir, marker["backup"])
    else:
        restored = True
        try:
            transport.start(settings.container)
            started = True
        except transport.TransportError:
            started = False
    if restored and started:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        if workdir:
            shutil.rmtree(workdir, ignore_errors=True)
    return True


def apply_edits(settings, edits, app_dir=None, status_timeout=60, expected=None):
    """Run one apply. Returns ``{"ok", "code", "log", "backup", "error"}``."""
    app_dir = app_dir or os.path.dirname(os.path.abspath(__file__))
    os.makedirs(_cache_dir(), exist_ok=True)
    workdir = tempfile.mkdtemp(prefix="apply-", dir=_cache_dir())
    log = []
    backup = None
    stopped = False
    done = False
    keep = False
    wrote_marker = False
    marker = marker_path()

    try:
        with Lock(lock_path()):
            _write_json(os.path.join(workdir, "edits.json"), edits)
            if expected is not None:
                _write_json(os.path.join(workdir, "expected.json"), expected)
            # Finish any apply that died before this one starts, while holding the
            # lock and before writing our own marker.
            if recover(settings, app_dir):
                log.append("recovered an apply that did not finish")
            record = {"workdir": workdir, "backup": None, "pid": os.getpid(),
                      "started_at": time.time()}
            _write_marker(marker, record)
            wrote_marker = True
            try:
                transport.stop(settings.container)
                stopped = True
                log.append("stopped " + settings.container)

                out = _step(settings, workdir, "backup", app_dir, EXIT_FAILED)
                log.append(out)
                if BACKUP_LINE not in out:
                    raise ApplyError(EXIT_FAILED, "the backup step named no backup", log)
                backup = out.split(BACKUP_LINE, 1)[1].strip().splitlines()[0]
                record["backup"] = backup
                _write_marker(marker, record)

                log.append(_step(settings, workdir, "apply", app_dir, EXIT_REFUSED))
                log.append(_step(settings, workdir, "validate", app_dir, EXIT_REFUSED))
                log.append(_step(settings, workdir, "write", app_dir, EXIT_FAILED))

                transport.start(settings.container)
                log.append("started " + settings.container)

                identity = _read_json(os.path.join(workdir, "identity.json"))
                deadline = time.time() + status_timeout
                while True:
                    try:
                        line = transport.status(settings.container)
                    except transport.TransportError:
                        line = ""          # still booting: the game answers only once loaded
                    if _status_matches(line, identity):
                        log.append("status ok: %s rank %s"
                                   % (identity.get("name"), identity.get("rank")))
                        break
                    if time.time() >= deadline:
                        raise ApplyError(
                            EXIT_STATUS_TIMEOUT,
                            "the server did not come back healthy within %ss" % status_timeout,
                            log)
                    time.sleep(1)
                done = True
                return {"ok": True, "code": 0, "log": log, "backup": backup, "error": None}
            except ApplyError as error:
                error.log = log            # same list: the rollback line lands here too
                raise
            finally:
                if stopped and not done:
                    restored, started_again, message = _restore(settings, workdir, app_dir, backup)
                    log.append(message)
                    # Anything short of "backup back AND container running" leaves the
                    # marker: the next start of the app must finish the job.
                    keep = not (restored and started_again)
    except ApplyBusy as error:
        return {"ok": False, "code": EXIT_BUSY, "log": log, "backup": backup, "error": str(error)}
    except ApplyError as error:
        return {"ok": False, "code": error.code, "log": error.log, "backup": backup,
                "error": str(error)}
    except transport.TransportError as error:
        return {"ok": False, "code": error.returncode or EXIT_FAILED, "log": log,
                "backup": backup, "error": str(error)}
    finally:
        # Only our own marker, and only when nothing is left to repair.
        if not keep and wrote_marker:
            _forget(marker, workdir, pid=os.getpid())
        elif not keep and not wrote_marker:
            shutil.rmtree(workdir, ignore_errors=True)
