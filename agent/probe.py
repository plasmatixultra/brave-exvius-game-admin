#!/usr/bin/env python3
"""Spike probe (Task 2): load a player save with the SERVER's own player_state, in the server image.

Reads argv and the environment only (MST_DIR, TEXT_DIR, FFBE_DATA, PYTHONPATH); sets nothing itself.
Writes nothing unless --save is given. Everything reusable lives in the Task 3 agent, not here.

Refuses a missing or unparsable input: PlayerState.load would silently return a FRESH account
(player_state.py:2453-2467), which is the trap the agent must guard.

    python3 probe.py <save.json> [--save <out.json>] [--coherence]
"""

import json
import os
import sys
import time

USAGE = "usage: probe.py <save.json> [--save <out.json>] [--coherence]"


def refuse(message):
    print(f"probe: REFUSING: {message} (PlayerState.load would return a FRESH account instead)",
          file=sys.stderr, flush=True)
    return 2


def main(argv):
    path = out = None
    coherence = False
    args = iter(argv[1:])
    for arg in args:
        if arg == "--save":
            out = next(args, None)
            if not out:
                print(USAGE, file=sys.stderr, flush=True)
                return 2
        elif arg == "--coherence":
            coherence = True
        elif arg.startswith("-") or path is not None:
            print(USAGE, file=sys.stderr, flush=True)
            return 2
        else:
            path = arg
    if path is None:
        print(USAGE, file=sys.stderr, flush=True)
        return 2

    # The guard runs BEFORE the server package is imported, so a bad path costs nothing and fails loudly.
    if not os.path.exists(path):
        return refuse(f"{path} does not exist")
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as exc:
        return refuse(f"{path} is not readable JSON ({exc!r})")
    if not isinstance(raw, dict):
        return refuse(f"{path} is JSON but not an object ({type(raw).__name__})")

    t0 = time.perf_counter()
    import player_state
    import_seconds = time.perf_counter() - t0

    t0 = time.perf_counter()
    ps = player_state.PlayerState.load(path, slot="campaign")
    load_seconds = time.perf_counter() - t0

    data = ps.data
    name = data["identity"]["handle_name"]
    gil = data["currencies"]["gil"]
    roster = len(data["roster"])
    summary = {"handle_name": name, "gil": gil, "roster": roster,
               "import_seconds": round(import_seconds, 3),
               "load_seconds": round(load_seconds, 3)}

    print(f"handle_name: {name}", flush=True)
    print(f"gil: {gil}", flush=True)
    print(f"currencies: {json.dumps(data['currencies'], sort_keys=True)}", flush=True)
    print(f"roster: {roster}", flush=True)
    print(f"import_seconds: {import_seconds:.3f}", flush=True)
    print(f"load_seconds: {load_seconds:.3f}", flush=True)

    if coherence:
        issues = player_state.state_coherence_issues(data)
        summary["coherence_issues"] = issues
        print(f"coherence_issues: {json.dumps(issues)}", flush=True)

    if out:
        saved = ps.save(out, force=True)
        summary["saved"] = {"path": out, "ok": saved}
        print(f"saved: {out} ok={saved}", flush=True)
        if not saved:
            return 1

    print(f"summary: {json.dumps(summary, sort_keys=True)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
