"""The write path's front door: refuse bad payloads, then run one apply.

Everything dangerous lives in :mod:`applysave` (stop, backup, edit through the
image, swap, start, poll, roll back). This module only refuses edits the agent
could never apply and keeps the ``{ok, backup, log, error}`` shape the HTTP
layer speaks.

    pipeline.apply(edits) -> {"ok": bool, "backup": str|None, "log": [str], "error": str|None}
"""

import applysave
import compat
import config
import engine
import localdata

# The edit groups the agent knows how to apply (plan Task 3; `progress` is opaque until project two).
GROUPS = ("currencies", "tickets", "points", "materials", "cards", "card_levels",
          "units", "abilities", "progress")


def preflight(edits):
    """Refuse locally, before anything is uploaded or the container is touched."""
    if not isinstance(edits, dict):
        raise ValueError("edits: expected an object")
    if not any(group in edits for group in GROUPS):
        raise ValueError("no edits: at least one edit group is required")



def engine_problem(settings):
    """None when the image can run the agent, else a sentence naming what is missing.

    Checked before the container stops: an engine that has moved on must cost the
    player nothing, not half an apply.
    """
    try:
        engine_dir = engine.ensure(settings, localdata.CACHE)
    except engine.EngineError as error:
        return str(error)
    result = compat.check(engine_dir, settings.data_dir)
    if result["ok"]:
        return None
    shown = ", ".join(result["missing"][:8])
    more = len(result["missing"]) - 8
    return ("this engine cannot run the agent: missing %s%s. The app was built against "
            "ffbe-ios-server:local; update compat.json only if the engine change is benign."
            % (shown, (" (+%d more)" % more) if more > 0 else ""))


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

    drift = engine_problem(settings)
    if drift:
        return {"ok": False, "backup": None, "log": [], "error": drift}

    result = applysave.apply_edits(settings, edits, app_dir=app_dir,
                                   status_timeout=status_timeout)
    return {"ok": result["ok"], "backup": result["backup"], "log": result["log"],
            "error": result["error"]}


