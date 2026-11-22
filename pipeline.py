"""Local driver for the athena-side apply.

Everything dangerous happens in `remote/apply.sh`, on athena, under one lock and one trap — so a
lost ssh connection or a killed local process still restores the save. This module only stages the
payload, runs the script once, and turns its output into `{ok, backup, log, error}`.

    pipeline.apply(edits) -> {"ok": bool, "backup": str|None, "log": [str], "error": str|None}
"""

import json
import os
import shutil
import tempfile
import time

import config
import remote
from remote import RemoteError

ROOT = os.path.dirname(os.path.abspath(__file__))
AGENT_DIR = os.path.join(ROOT, "agent")
APPLY_SH = os.path.join(ROOT, "remote", "apply.sh")
REMOTE_ROOT = "exvius-admin-tests/apply"

# The edit groups the agent knows how to apply (plan Task 3; `progress` is opaque until project two).
GROUPS = ("currencies", "tickets", "points", "materials", "cards", "card_levels",
          "units", "abilities", "progress")


def preflight(edits):
    """Refuse locally, before anything is uploaded or the container is touched."""
    if not isinstance(edits, dict):
        raise ValueError("edits: expected an object")
    if not any(group in edits for group in GROUPS):
        raise ValueError("no edits: at least one edit group is required")


QUERY_DIR = "exvius-admin-tests/query"
CONTAINER_AGENT = "/tmp/ffbe-admin/agent"
CONTAINER_SAVE = "/tmp/ffbe-admin/save.json"
CONTAINER_EDITS = "/tmp/ffbe-admin/edits.json"
MUTATING_OPS = ("apply",)
_SEEDED = False


class AgentError(RuntimeError):
    """The agent refused, or could not be reached."""


def query(op, edits=None, flags=None):
    """Run one READ-ONLY agent op on a copy of the live save and return its `result`.

    Fast path: `docker exec` into the running container, which is ~5x quicker than `docker run`
    (no container to create). The agent tree is copied into the container once per session
    (`_SEEDED`) and re-seeded automatically if the container was recreated.
    """
    global _SEEDED
    if op in MUTATING_OPS:
        raise AgentError(f"{op} is not a query op; use pipeline.apply")
    work = f"~/{QUERY_DIR}"
    remote.run(f"mkdir -p {work}")
    remote.put_tree(AGENT_DIR, f"{work}/agent")
    args = [op, CONTAINER_SAVE]
    if edits is not None:
        local = tempfile.mkdtemp(prefix="ffbe-query-")
        try:
            path = os.path.join(local, "edits.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(edits, fh)
            remote.put(path, f"{work}/edits.json")
        finally:
            shutil.rmtree(local, ignore_errors=True)
        args.append(CONTAINER_EDITS)
    for name, value in (flags or {}).items():
        args.append(f"--{name}")
        args.append(str(value))
    command = _exec_script(work, args, seed=not _SEEDED)
    try:
        result = remote.run(command, timeout=120)
    except RemoteError as exc:
        text = exc.stderr or str(exc)
        if "can't open file" in text or "Could not find the file /tmp/ffbe-admin" in text:
            _SEEDED = False
            try:
                result = remote.run(_exec_script(work, args, seed=True), timeout=120)
            except RemoteError as retry_exc:
                raise AgentError((retry_exc.stderr or str(retry_exc)).strip()) from retry_exc
        else:
            raise AgentError((exc.stderr or str(exc)).strip()) from exc
    _SEEDED = True
    try:
        payload = json.loads(result.stdout)
    except ValueError as exc:
        raise AgentError(f"the agent printed no JSON: {result.stdout[:200]!r}") from exc
    if not payload.get("ok"):
        raise AgentError(payload.get("error") or "the agent refused")
    return payload["result"]


def _exec_script(work, args, seed):
    """One ssh invocation: stage the save, run the agent in the live container.

    The seed (mkdir + the agent tree) comes FIRST: every other copy targets /tmp/ffbe-admin.
    """
    lines = []
    if seed:
        lines += [f"docker exec {config.CONTAINER} mkdir -p /tmp/ffbe-admin",
                  f"docker cp {work}/agent {config.CONTAINER}:{CONTAINER_AGENT}"]
    lines += [f"cp {config.SAVE_HOST} {work}/save.json",
              f"docker cp {work}/save.json {config.CONTAINER}:{CONTAINER_SAVE}"]
    if edits_path_in_args(args):
        lines.insert(len(lines) - 1,
                     f"docker cp {work}/edits.json {config.CONTAINER}:{CONTAINER_EDITS}")
    lines.append(
        f"docker exec -e PYTHONPATH=/app/server "
        f"-e MST_DIR=/srv/ffbe/data/static/mst_decoded "
        f"-e TEXT_DIR=/srv/ffbe/data/static/text_decoded "
        f"-e FFBE_DATA=/tmp/scratch {config.CONTAINER} "
        f"python3 {CONTAINER_AGENT}/agent.py " + " ".join(args))
    return " && ".join(lines)


def edits_path_in_args(args):
    return any(str(arg).endswith("edits.json") for arg in args)


def _remote_workdir():
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return f"~/{REMOTE_ROOT}/{stamp}-{os.getpid()}"


def apply(edits):
    """Stage, run one apply under the lock, and report. Never raises for a refused apply."""
    try:
        preflight(edits)
    except ValueError as exc:
        return {"ok": False, "backup": None, "log": [], "error": str(exc)}

    work = tempfile.mkdtemp(prefix="ffbe-apply-")
    edits_path = os.path.join(work, "edits.json")
    with open(edits_path, "w", encoding="utf-8") as fh:
        json.dump(edits, fh)
    remote_work = _remote_workdir()
    log = []
    try:
        remote.run(f"mkdir -p {remote_work}")
        remote.put_tree(AGENT_DIR, f"{remote_work}/agent")
        remote.put(APPLY_SH, f"{remote_work}/apply.sh")
        remote.put(edits_path, f"{remote_work}/edits.json")
        try:
            result = remote.run(f"bash {remote_work}/apply.sh {remote_work}", timeout=600)
        except RemoteError as exc:
            log = [line for line in (exc.stderr or "").splitlines() if line]
            if exc.returncode == 99:
                return {"ok": False, "backup": None, "log": log, "error": "apply already running"}
            return {"ok": False, "backup": _backup_from(log), "log": log,
                    "error": log[-1] if log else f"apply failed with exit {exc.returncode}"}
        log = [line for line in (result.stdout or "").splitlines() if line]
        if not any(line.strip() == "apply complete" for line in log):
            return {"ok": False, "backup": _backup_from(log), "log": log,
                    "error": "the apply did not complete (exit 0 without the completion marker)"}
        cleanup = f"rm -rf {remote_work}"      # a successful run needs no forensics
        remote.run(cleanup)
        return {"ok": True, "backup": _backup_from(log), "log": log, "error": None}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _backup_from(log_lines):
    for line in log_lines:
        if line.startswith("backup "):
            return line[len("backup "):].strip()
    return None
