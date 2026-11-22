"""The write path's front door: refuse bad payloads, then run one apply.

Everything dangerous lives in :mod:`applysave` (stop, backup, edit through the
image, swap, start, poll, roll back). This module only refuses edits the agent
could never apply and keeps the ``{ok, backup, log, error}`` shape the HTTP
layer speaks.

    pipeline.apply(edits) -> {"ok": bool, "backup": str|None, "log": [str], "error": str|None}
"""

import applysave
import config

# The edit groups the agent knows how to apply (plan Task 3; `progress` is opaque until project two).
GROUPS = ("currencies", "tickets", "points", "materials", "cards", "card_levels",
          "units", "abilities", "progress")


def preflight(edits):
    """Refuse locally, before anything is uploaded or the container is touched."""
    if not isinstance(edits, dict):
        raise ValueError("edits: expected an object")
    if not any(group in edits for group in GROUPS):
        raise ValueError("no edits: at least one edit group is required")



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


