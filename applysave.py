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


class ApplyBusy(RuntimeError):
    """Another apply holds the lock."""


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


def _forget(marker, workdir):
    try:
        os.unlink(marker)
    except FileNotFoundError:
        pass
    shutil.rmtree(workdir, ignore_errors=True)


def _read_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _run_files(settings, workdir, verb, app_dir, extra=(), timeout=900):
    done = transport.run_in_image(
        settings.image,
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
    return (status.get("has_save") is True
            and status.get("name") == identity.get("name")
            and int(status.get("rank", -1)) == int(identity.get("rank", -2)))


def _restore(settings, workdir, app_dir, backup, log):
    """Put the backup back and start the container. Never raises."""
    if not backup:
        try:
            transport.start(settings.container)
        except transport.TransportError:
            pass
        return "ROLLBACK: no backup was taken; the live save is untouched"
    try:
        # The workdir belongs to the image's user once the backup has run; the
        # path travels as an argument so the host never writes there.
        out = _run_files(settings, workdir, "restore", app_dir, extra=[backup])
    except (transport.TransportError, OSError) as error:
        out = "ROLLBACK FAILED: the backup is at %s (%s)" % (backup, error)
    try:
        transport.start(settings.container)
    except transport.TransportError:
        out += "; the container could not be started"
    return out


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
    log = []
    try:
        workdir = marker.get("workdir")
        if marker.get("backup") and workdir and os.path.isdir(workdir):
            _restore(settings, workdir, app_dir, marker["backup"], log)
        else:
            transport.start(settings.container)
    except transport.TransportError:
        pass
    finally:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
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
    marker = marker_path()

    try:
        with Lock(lock_path()):
            _write_json(os.path.join(workdir, "edits.json"), edits)
            if expected is not None:
                _write_json(os.path.join(workdir, "expected.json"), expected)
            record = {"workdir": workdir, "backup": None, "pid": os.getpid(),
                      "started_at": time.time()}
            _write_marker(marker, record)
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
                    outcome = _restore(settings, workdir, app_dir, backup, log)
                    log.append(outcome)
                    # A rollback that failed leaves the marker behind: the next
                    # start of the app must try again, not forget the backup.
                    if "ROLLBACK FAILED" in outcome:
                        keep = True
    except ApplyBusy as error:
        return {"ok": False, "code": EXIT_BUSY, "log": log, "backup": backup, "error": str(error)}
    except ApplyError as error:
        return {"ok": False, "code": error.code, "log": error.log, "backup": backup,
                "error": str(error)}
    except transport.TransportError as error:
        return {"ok": False, "code": error.returncode or EXIT_FAILED, "log": log,
                "backup": backup, "error": str(error)}
    finally:
        # A failed rollback keeps both the marker and the workdir: recover() needs them.
        if not keep:
            _forget(marker, workdir)
