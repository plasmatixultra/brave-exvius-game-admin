"""What the agent needs from the engine, checked before anything is stopped.

`compat.json` lists the modules, attributes and master-table columns the agent
actually touches. It is deliberately small and explicit: an engine that has
moved on should be refused with a name, not discovered halfway through an
apply by an AttributeError.
"""

import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(HERE, "compat.json")


def manifest(path=None):
    with open(path or MANIFEST, encoding="utf-8") as handle:
        return json.load(handle)


def _load_module(name, engine_dir):
    path = os.path.join(engine_dir, name + ".py")
    if not os.path.isfile(path):
        return None
    spec = importlib.util.spec_from_file_location("_compat_%s" % name, path)
    module = importlib.util.module_from_spec(spec)
    saved = sys.path[:]
    sys.path.insert(0, engine_dir)
    try:
        spec.loader.exec_module(module)
    except Exception:                      # noqa: BLE001 - an unimportable module is drift too
        return None
    finally:
        sys.path[:] = saved
    return module


def _columns(path):
    """The first row's keys: every table is one JSON object per line."""
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    return set(json.loads(line))
        return set()                       # an empty table cannot be checked
    except (OSError, ValueError):
        return None


def check(engine_dir, data_dir, path=None):
    """Return ``{"ok": bool, "missing": [str]}``; names say what to update."""
    wanted = manifest(path)
    missing = []
    for name, attributes in (wanted.get("modules") or {}).items():
        module = _load_module(name, engine_dir)
        if module is None:
            missing.append(name)
            continue
        for attribute in attributes:
            if not hasattr(module, attribute):
                missing.append("%s.%s" % (name, attribute))
    mst = os.path.join(data_dir, "mst_decoded")
    for table, columns in (wanted.get("columns") or {}).items():
        found = _columns(os.path.join(mst, table + ".ndjson"))
        if found is None:
            missing.append(table)
            continue
        if not found:                      # nothing to check against
            continue
        for column in columns:
            if column not in found:
                missing.append("%s.%s" % (table, column))
    return {"ok": not missing, "missing": missing}
