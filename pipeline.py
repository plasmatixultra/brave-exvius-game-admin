"""Local driver for the athena-side apply.

Everything dangerous happens in `remote/apply.sh`, on athena, under one lock and one trap — so a
lost ssh connection or a killed local process still restores the save. This module only stages the
payload, runs the script once, and turns its output into `{ok, backup, log, error}`.

    pipeline.apply(edits) -> {"ok": bool, "backup": str|None, "log": [str], "error": str|None}
"""

import json
import os
import shlex
import shutil
import tempfile
import time

import applysave
import config
import remote
from remote import RemoteError

ROOT = os.path.dirname(os.path.abspath(__file__))
AGENT_DIR = os.path.join(ROOT, "agent")
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
        # quote every arg: `--id` values come from HTTP query params and are joined into a shell line
        f"python3 {CONTAINER_AGENT}/agent.py " + " ".join(shlex.quote(str(arg)) for arg in args))
    return " && ".join(lines)


def edits_path_in_args(args):
    return any(str(arg).endswith("edits.json") for arg in args)


def apply(edits, settings=None, app_dir=None, status_timeout=60):
    """Run one apply and report. Never raises for a refused apply.

    The whole sequence lives in :mod:`applysave`; this wrapper only refuses bad
    payloads locally and keeps the ``{ok, backup, log, error}`` shape the HTTP
    layer speaks.
    """
    try:
        preflight(edits)
    except ValueError as exc:
        return {"ok": False, "backup": None, "log": [], "error": str(exc)}

    try:
        settings = settings or config.load()
    except config.SettingsError as exc:
        return {"ok": False, "backup": None, "log": [], "error": str(exc)}

    result = applysave.apply_edits(settings, edits, app_dir=app_dir,
                                   status_timeout=status_timeout)
    return {"ok": result["ok"], "backup": result["backup"], "log": result["log"],
            "error": result["error"]}


