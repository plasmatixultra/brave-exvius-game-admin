#!/usr/bin/env python3
"""Agent CLI: edit one player save with the server's own `player_state`.

Runs INSIDE the server image (context A — see `docs/superpowers/notes/agent-context.md`):

    docker run --rm --entrypoint python3 \
      -e MST_DIR=... -e TEXT_DIR=... -e FFBE_DATA=<scratch dir> -e PYTHONPATH=/app/server \
      -v <agent dir>:/agent:ro -v <work dir>:/work ffbe-ios-server:local \
      /agent/agent.py apply /work/save.json /work/edits.json --out /work/staging.json

Prints exactly one JSON object on stdout: `{"ok": bool, "error": str|null, "result": ...}`.
Exit 0 when ok, 2 when the agent refused (nothing written), 1 on an unexpected crash.

    summary  <save>
    catalog  <save>
    preview  <save> <edits|->
    apply    <save> <edits|-> [--out PATH] [--workdir DIR]
    validate <save> [--baseline PATH] [--expected PATH]

`PlayerState.load` returns a FRESH account — silently, and also named "Rain" — for a missing or
corrupt file, so every entry point guards the path and parses the JSON before the server sees it.
"""

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
# The package root first: `import agent` must find this package, not this script.
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import player_state  # noqa: E402  (needs PYTHONPATH=/app/server inside the image)
from agent import ops_abilities, ops_basic, ops_cards, ops_items, ops_units  # noqa: E402

USAGE = """usage:
  agent.py summary  <save>
  agent.py catalog  <save>
  agent.py preview  <save> <edits|->
  agent.py apply    <save> <edits|-> [--out PATH] [--workdir DIR]
  agent.py validate <save> [--baseline PATH] [--expected PATH]"""

# The one definition of the edits schema, applied in this order (plan Task 3).
GROUP_ORDER = ("currencies", "tickets", "points", "materials", "cards",
               "card_levels", "units", "abilities", "progress")

OPS = ("summary", "catalog", "preview", "apply", "validate", "recipe", "abilities")
FLAG_NAMES = ("out", "workdir", "baseline", "expected", "table", "id", "master")


# --------------------------------------------------------------------------- loading


def load_raw(path):
    """Parse a save file. Refuses before the server code ever sees it."""
    if not path or not os.path.exists(path):
        raise ValueError(f"{path} does not exist (PlayerState.load would return a FRESH account)")
    if not os.path.isfile(path):
        raise ValueError(f"{path} is not a regular file")
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except OSError as exc:
        raise ValueError(f"{path} is not readable ({exc})") from None
    except ValueError as exc:
        raise ValueError(f"{path} is not valid JSON ({exc})") from None
    if not isinstance(raw, dict):
        raise ValueError(f"{path} is JSON but not an object")
    return raw


def load_state(path):
    """`PlayerState.load` after the guard. Raises ValueError when the state is incoherent
    (`load` itself runs `state_coherence_issues`)."""
    load_raw(path)
    return player_state.PlayerState.load(path, slot="campaign")


# --------------------------------------------------------------------------- small helpers


def _int_or_zero(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _int_list(text):
    out = []
    for part in str(text or "").split(","):
        try:
            out.append(int(part))
        except (TypeError, ValueError):
            break
    return out


def _descriptor_required(text):
    """Field 4 of a trust descriptor (`kind:id:count:required:...`), or None."""
    parts = str(text or "").split(":")
    if len(parts) < 4 or not parts[3].strip().isdigit():
        return None
    return int(parts[3])


_UNIT_NAMES = None


def unit_names():
    """{master_id: English name} from `F_TEXT_UNITS_NAME` (key `MST_UNIT_NAME_<id>`)."""
    global _UNIT_NAMES
    if _UNIT_NAMES is None:
        names = {}
        path = os.path.join(player_state.TEXT_DIR, "F_TEXT_UNITS_NAME.ndjson")
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    row = json.loads(line)
                    key = str(row.get("key", ""))
                    if key.startswith("MST_UNIT_NAME_"):
                        values = row.get("v") or []
                        names[key[len("MST_UNIT_NAME_"):]] = str(values[0]) if values else ""
        except (OSError, ValueError):
            names = {}
        _UNIT_NAMES = names
    return _UNIT_NAMES


_VISION_NAMES = None


def vision_card_names():
    """{vc_id: English name} from `F_TEXT_VISION_CARD_NAME` (key `MST_VISION_CARD_NAME_<id>`)."""
    global _VISION_NAMES
    if _VISION_NAMES is None:
        names = {}
        path = os.path.join(player_state.TEXT_DIR, "F_TEXT_VISION_CARD_NAME.ndjson")
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    row = json.loads(line)
                    key = str(row.get("key", ""))
                    if key.startswith("MST_VISION_CARD_NAME_"):
                        values = row.get("v") or []
                        names[key[len("MST_VISION_CARD_NAME_"):]] = str(values[0]) if values else ""
        except (OSError, ValueError):
            names = {}
        _VISION_NAMES = names
    return _VISION_NAMES


_LB_LEVELS = None


def lb_levels():
    """{LB id: [level, ...]} from `F_LIMITBURST_LV_MST` (level column `7wV3QZ80`)."""
    global _LB_LEVELS
    if _LB_LEVELS is None:
        out = {}
        for row in player_state._load_ndjson("F_LIMITBURST_LV_MST.ndjson").get("__rows__", []):
            out.setdefault(str(row.get("PR2HIc71")), []).append(_int_or_zero(row.get("7wV3QZ80")))
        _LB_LEVELS = out
    return _LB_LEVELS


def lb_cap(master_id, master):
    """min(highest LB level in the table, the unit's own `ENwn42DV`), or None without an LB row."""
    burst = player_state.limit_burst(master_id)
    if burst is None:
        return None
    hard = max(1, _int_or_zero(master.get("ENwn42DV")))
    levels = lb_levels().get(str(burst.get("PR2HIc71"))) or []
    return min(max(levels), hard) if levels else hard


# --------------------------------------------------------------------------- validation


def check_items(data):
    issues = []
    for row in data.get("inventory", {}).get("items") or []:
        try:
            item_type = int(row.get("item_type"))
            item_id = str(row.get("item_id"))
            count = int(row.get("count", 0))
        except (TypeError, ValueError):
            issues.append(f"inventory: unparsable row {row!r}")
            continue
        if item_type not in (ops_basic.BAG_TYPE, ops_basic.IMPORTANT_TYPE):
            continue
        cap = ops_basic.stack_cap(item_type, item_id)
        if cap is None:
            issues.append(f"inventory: {item_type}/{item_id} is not in the master table")
        elif count < 0 or count > cap:
            issues.append(f"inventory: {item_type}/{item_id} count {count} outside 0..{cap}")
    return issues


def check_units(data, items=None):
    issues = []
    for unit in data.get("roster") or []:
        instance = str(unit.get("instance_id", "?"))
        master_id = str(unit.get("master_id", ""))
        master = player_state.unit_mst(master_id)
        if master is None:
            issues.append(f"roster {instance}: unknown master id {master_id}")
            continue
        ex_level = _int_or_zero(unit.get("nv_ex_level"))
        if not 0 <= ex_level <= 3:
            issues.append(f"roster {instance} ({master_id}): nv_ex_level {ex_level} outside 0..3")
        cap = lb_cap(master_id, master)
        level = _int_or_zero(unit.get("lb_level"))
        if cap is not None and not 1 <= level <= cap:
            issues.append(f"roster {instance} ({master_id}): lb_level {level} outside 1..{cap}")
        points = unit.get("affinity_points") or ["", ""]
        for index, column in ((0, "9mu4boy7"), (1, "C74EmZ1I")):
            required = _descriptor_required(master.get(column))
            value = _int_or_zero(points[index]) if index < len(points) else 0
            if required is not None and value > required:
                label = "trust" if index == 0 else "super trust"
                issues.append(f"roster {instance} ({master_id}): {label} {value} above the cap {required}")
        pots = unit.get("stat_pots") or [0] * 6
        doors = unit.get("stat_doors") or [0] * 6
        for index, token in enumerate(player_state.STAT_TOKENS):
            descriptor = _int_list(master.get(token))
            pot_cap = descriptor[2] if len(descriptor) > 2 else 0
            door_cap = descriptor[3] if len(descriptor) > 3 else 0
            pot = _int_or_zero(pots[index]) if index < len(pots) else 0
            door = _int_or_zero(doors[index]) if index < len(doors) else 0
            if door > door_cap or pot > pot_cap + door:
                issues.append(f"roster {instance} ({master_id}): stat {index} "
                              f"pot {pot}/door {door} above {pot_cap}/{door_cap}")
    return issues


def check_cards(data):
    """Every card must satisfy the server's own `_card_progress` (exvius_actions_shops.py:1159)."""
    issues = []
    from exvius_actions_shops import _card_progress
    masters = player_state._load_ndjson("F_VISION_CARD_MST.ndjson", player_state.VISION_CARD_KEY)
    seen = set()
    for card in data.get("vision_cards") or []:
        user_vc_id = str(card.get("user_vc_id", "?"))
        if user_vc_id in seen:
            issues.append(f"vision card {user_vc_id}: duplicate user_vc_id")
        seen.add(user_vc_id)
        master = masters.get(str(card.get("vc_id")))
        if master is None:
            issues.append(f"vision card {user_vc_id}: unknown vc_id {card.get('vc_id')}")
            continue
        try:
            _card_progress(card, master)
        except ValueError as exc:
            issues.append(f"vision card {user_vc_id}: {exc}")
    return issues


def check_baseline(staged, baseline, expected):
    """Identity, untouched-currency and roster-count checks against the pre-apply backup."""
    issues = []
    base_identity = baseline.get("identity") or {}
    staged_identity = staged.get("identity") or {}
    for field in ("user_id", "handle_name"):
        if str(staged_identity.get(field, "")) != str(base_identity.get(field, "")):
            issues.append(f"identity.{field}: {staged_identity.get(field)!r} != baseline "
                          f"{base_identity.get(field)!r} (a fresh account?)")
    base_rank = _int_or_zero((baseline.get("rank") or {}).get("level"))
    staged_rank = _int_or_zero((staged.get("rank") or {}).get("level"))
    if staged_rank != base_rank:
        issues.append(f"rank.level: {staged_rank} != baseline {base_rank}")
    want_currencies = expected.get("currencies") or {}
    for key in ops_basic.CURRENCY_KEYS:
        want = _int_or_zero((baseline.get("currencies") or {}).get(key)) + _int_or_zero(want_currencies.get(key))
        got = _int_or_zero((staged.get("currencies") or {}).get(key))
        if got != want:
            issues.append(f"currencies.{key}: {got} != baseline+expected {want}")
    want_roster = len(baseline.get("roster") or []) + _int_or_zero(expected.get("roster_added"))
    got_roster = len(staged.get("roster") or [])
    if got_roster != want_roster:
        issues.append(f"roster: {got_roster} rows != baseline+expected {want_roster}")
    return issues


def validate_state(data, baseline=None, expected=None):
    issues = []
    issues += check_items(data)
    issues += check_units(data)
    issues += check_cards(data)
    if baseline is not None:
        issues += check_baseline(data, baseline, expected or {})
    return issues


# --------------------------------------------------------------------------- edit groups


def apply_currencies(ps, value):
    return ops_basic.apply_currencies(ps, {"currencies": value})


def apply_tickets(ps, value):
    return ops_basic.apply_tickets(ps, value)


def apply_points(ps, value):
    return ops_items.apply_points(ps, value)


def apply_materials(ps, value):
    return ops_items.apply_materials(ps, value)


def apply_cards(ps, value):
    return ops_cards.apply_cards(ps, value)


def apply_card_levels(ps, value):
    if not isinstance(value, list):
        raise ValueError("card_levels: expected a list of {user_vc_id, level}")
    for row in value:
        if not isinstance(row, dict) or "user_vc_id" not in row or "level" not in row:
            raise ValueError(f"card_levels: every row needs user_vc_id and level, got {row!r}")
        ops_cards.set_card_level(ps, row["user_vc_id"], row["level"])
    return None


def apply_units(ps, value):
    if not isinstance(value, list):
        raise ValueError("units: expected a list of {instance_id, spec}")
    built = []
    for row in value:
        if not isinstance(row, dict):
            raise ValueError(f"units: every row must be an object, got {row!r}")
        unit = ops_units.build_unit(ps, row.get("spec") or {}, row.get("instance_id"))
        built.append({"instance_id": str(unit["instance_id"]),
                      "master_id": str(unit["master_id"]),
                      "level": int(unit.get("level") or 1)})
    return {"units": built}


def apply_abilities(ps, value):
    if not isinstance(value, list):
        raise ValueError("abilities: expected a list of {instance_id, recipe_id}")
    done = []
    for row in value:
        if not isinstance(row, dict) or "instance_id" not in row or "recipe_id" not in row:
            raise ValueError(f"abilities: every row needs instance_id and recipe_id, got {row!r}")
        done.append(ops_abilities.apply_ability(ps, row["instance_id"], row["recipe_id"],
                                                grant_materials=bool(row.get("grant_materials")),
                                                force_exbonus=bool(row.get("force_exbonus"))))
    return {"abilities": done}


def apply_progress(ps, value):
    """Opaque here: the questline project owns `progress` and plugs into this table."""
    return {"ignored": True, "reason": "owned by the questline project"}


APPLIERS = {
    "currencies": apply_currencies,
    "tickets": apply_tickets,
    "points": apply_points,
    "materials": apply_materials,
    "cards": apply_cards,
    "card_levels": apply_card_levels,
    "units": apply_units,
    "abilities": apply_abilities,
    "progress": apply_progress,
}


def apply_groups(ps, edits, results):
    for group in GROUP_ORDER:
        if group not in edits:
            continue
        applier = APPLIERS.get(group)
        if applier is None:
            raise ValueError(f"edit group {group!r} is not implemented by this agent yet")
        results[group] = applier(ps, edits[group])


def requested_currencies(edits):
    rows = edits.get("currencies") or {}
    if not isinstance(rows, dict):
        raise ValueError("currencies: expected an object of name -> integer")
    return {key: (ops_basic.as_int(rows[key], key) if key in rows else 0)
            for key in ops_basic.CURRENCY_KEYS}


def requested_roster(edits):
    """Roster rows the edits should add: built units plus the unit rewards they trigger."""
    return ops_units.expected_roster_added(edits)


def snapshot(ps):
    data = ps.data
    return {"currencies": {key: _int_or_zero((data.get("currencies") or {}).get(key))
                           for key in ops_basic.CURRENCY_KEYS},
            "roster": len(data.get("roster") or [])}


# --------------------------------------------------------------------------- ops


def op_summary(save, _edits, _flags):
    ps = load_state(save)
    data = ps.data
    names = unit_names()
    cards = player_state._load_ndjson("F_VISION_CARD_MST.ndjson", player_state.VISION_CARD_KEY)
    roster = []
    for unit in data.get("roster") or []:
        master_id = str(unit.get("master_id", ""))
        master = player_state.unit_mst(master_id) or {}
        points = unit.get("affinity_points") or []
        roster.append({
            "instance_id": str(unit.get("instance_id")),
            "master_id": master_id,
            "name": names.get(master_id) or master.get("G4L0YIB2") or master_id,
            "level": _int_or_zero(unit.get("level")) or 1,
            "rarity": _int_or_zero(master.get("9fW0TePj")) or _int_or_zero(unit.get("granted_rarity")) or 1,
            "nv_ex_level": _int_or_zero(unit.get("nv_ex_level")),
            "lb_level": _int_or_zero(unit.get("lb_level")),
            "trust": _int_or_zero(points[0]) if points else 0,
            "super_trust": _int_or_zero(points[1]) if len(points) > 1 else 0,
        })
    vision_cards = []
    card_names = vision_card_names()
    for card in data.get("vision_cards") or []:
        master = cards.get(str(card.get("vc_id"))) or {}
        vision_cards.append({
            "user_vc_id": str(card.get("user_vc_id")),
            "vc_id": str(card.get("vc_id")),
            "name": card_names.get(str(card.get("vc_id"))) or master.get("G4L0YIB2") or str(card.get("vc_id")),
            "level": _int_or_zero(card.get("level")) or 1,
            "cap": _int_or_zero(master.get("aU3o2D4t")) or 1,
        })
    held_materials = {}
    tickets_held = {}
    for row in (data.get("inventory") or {}).get("items") or []:
        kind = _int_or_zero(row.get("item_type"))
        if kind == ops_basic.BAG_TYPE:
            held_materials[str(row.get("item_id"))] = _int_or_zero(row.get("count"))
        elif kind == ops_basic.IMPORTANT_TYPE:
            tickets_held[str(row.get("item_id"))] = _int_or_zero(row.get("count"))
    bank = ((data.get("exvius_unit_points") or {}).get("banks") or {}).get("0") or {}
    currencies = data.get("currencies") or {}
    return {
        "gil": _int_or_zero(currencies.get("gil")),
        "lapis_free": _int_or_zero(currencies.get("lapis_free")),
        "lapis_paid": _int_or_zero(currencies.get("lapis_paid")),
        "roster": roster,
        "vision_cards": vision_cards,
        "materials_held": held_materials,
        "tickets_held": tickets_held,
        "exp_points": _int_or_zero(bank.get("XeRVw3s2")),
    }, []


def op_abilities(_save, _edits, flags):
    master_id = flags.get("master")
    if not master_id:
        raise ValueError("abilities needs --master <unit master id>")
    return {"recipes": ops_abilities.list_recipes(master_id)}, []


def op_recipe(_save, _edits, flags):
    if not flags.get("table") or not flags.get("id"):
        raise ValueError("recipe needs --table and --id")
    return ops_items.recipe_kit(flags["table"], flags["id"]), []


def op_catalog(_save, _edits, _flags):
    from agent import catalog
    return catalog.build(os.path.dirname(player_state.MST_DIR),
                         os.path.dirname(os.path.abspath(player_state.__file__))), []


def op_preview(save, edits_path, _flags):
    edits = read_edits(edits_path)
    if "unit_spec" in edits:
        return {"unit": ops_units.preview_unit(edits["unit_spec"])}, []
    ps = load_state(save)
    before = snapshot(ps)
    results = {}
    apply_groups(ps, edits, results)
    after = snapshot(ps)
    result = {
        "currencies": {key: {"before": before["currencies"][key], "after": after["currencies"][key],
                             "delta": after["currencies"][key] - before["currencies"][key]}
                       for key in ops_basic.CURRENCY_KEYS},
        "groups": results,
        "roster_added": after["roster"] - before["roster"],
    }
    return result, []


def op_apply(save, edits_path, flags):
    edits = read_edits(edits_path)
    if not any(group in edits for group in GROUP_ORDER):
        raise ValueError("no edits: at least one edit group is required")
    ps = load_state(save)
    before = snapshot(ps)
    results = {}
    apply_groups(ps, edits, results)
    after = snapshot(ps)

    # The declared expectation (the app's and, later, the questline preview's) is a promise about
    # the result; paying more or less than promised is a refusal, not a warning.
    actual_currencies = {key: after["currencies"][key] - before["currencies"][key]
                         for key in ops_basic.CURRENCY_KEYS}
    actual_roster = after["roster"] - before["roster"]
    declared = edits.get("expected") or {}
    declared_currencies = declared.get("currencies") or {}
    want_currencies = requested_currencies(edits)
    for key in ops_basic.CURRENCY_KEYS:
        want_currencies[key] += ops_basic.as_int(declared_currencies.get(key, 0), f"expected.{key}")
    want_roster = requested_roster(edits) + ops_basic.as_int(declared.get("roster_added", 0),
                                                             "expected.roster_added")
    if actual_currencies != want_currencies:
        raise ValueError(f"currency deltas {actual_currencies} do not match the request {want_currencies}")
    if actual_roster != want_roster:
        raise ValueError(f"roster changed by {actual_roster} rows; the request expected {want_roster}")

    out = flags.get("out") or save
    if not ps.save(out, force=True):
        raise RuntimeError(f"PlayerState.save refused to write {out}")
    staged = load_state(out)
    issues = validate_state(staged.data)
    if issues:
        raise ValueError("staged save failed validation: " + "; ".join(issues))

    workdir = flags.get("workdir")
    if workdir:
        os.makedirs(workdir, exist_ok=True)
        with open(os.path.join(workdir, "expected.json"), "w", encoding="utf-8") as fh:
            json.dump({"currencies": actual_currencies, "roster_added": actual_roster,
                       "extra": declared.get("extra") or {}}, fh, indent=1, sort_keys=True)
    return {"out": out, "groups": results, "currencies": actual_currencies,
            "roster_added": actual_roster}, []


def op_validate(save, _edits, flags):
    staged = load_state(save)
    baseline = load_raw(flags["baseline"]) if flags.get("baseline") else None
    expected = load_raw(flags["expected"]) if flags.get("expected") else None
    issues = validate_state(staged.data, baseline=baseline, expected=expected)
    return {"issues": issues}, issues


DISPATCH = {
    "summary": op_summary,
    "catalog": op_catalog,
    "preview": op_preview,
    "apply": op_apply,
    "validate": op_validate,
    "recipe": op_recipe,
    "abilities": op_abilities,
}


# --------------------------------------------------------------------------- CLI


def read_edits(path):
    if not path:
        raise ValueError("this op needs an edits file (or `-` for stdin)")
    if path == "-":
        try:
            edits = json.load(sys.stdin)
        except ValueError as exc:
            raise ValueError(f"stdin is not valid JSON ({exc})") from None
    else:
        edits = load_raw(path)
    if not isinstance(edits, dict):
        raise ValueError("edits: expected a JSON object")
    return edits


def parse(argv):
    if len(argv) < 2:
        raise ValueError(USAGE)
    op = argv[1]
    if op not in OPS:
        raise ValueError(f"unknown op {op!r}\n{USAGE}")
    positional, flags = [], {}
    rest = argv[2:]
    index = 0
    while index < len(rest):
        arg = rest[index]
        if arg.startswith("--"):
            name = arg[2:]
            if name not in FLAG_NAMES:
                raise ValueError(f"unknown flag {arg}\n{USAGE}")
            if index + 1 >= len(rest):
                raise ValueError(f"{arg} needs a value")
            flags[name] = rest[index + 1]
            index += 2
        else:
            positional.append(arg)
            index += 1
    if not positional:
        raise ValueError(f"{op} needs a save path\n{USAGE}")
    if len(positional) > 2:
        raise ValueError(f"too many arguments for {op}\n{USAGE}")
    return op, positional, flags


def main(argv):
    real_stdout = sys.stdout
    sys.stdout = sys.stderr  # the server's own prints must never corrupt the JSON reply
    payload = {"ok": False, "error": None, "result": None}
    code = 1
    try:
        op, positional, flags = parse(argv)
        edits_path = positional[1] if len(positional) > 1 else None
        result, issues = DISPATCH[op](positional[0], edits_path, flags)
        payload["result"] = result
        if issues:
            payload["error"] = "; ".join(issues)
            code = 2
        else:
            payload["ok"] = True
            code = 0
    except ValueError as exc:
        payload["error"] = str(exc)
        code = 2
    except Exception as exc:  # noqa: BLE001 - the CLI must always answer with one JSON object
        payload["error"] = f"{type(exc).__name__}: {exc}"
        code = 1
    finally:
        sys.stdout = real_stdout
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
