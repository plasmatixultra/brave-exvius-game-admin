"""Ability awakening (sublimation) — writing the learned-skill chain, and nothing else.

`sublimation_skill` (exvius_actions_shops.py:1247) stores what it learns in the feature state
`data["exvius_sublimation"]["skills"][<unit instance>][<root skill>] = <awakened skill id>`.
The `slots[<instance>]` flag beside it belongs to the SEPARATE paid action `unit_sublimation`
(:1231, cost `VISION_CARD_SACRIFICE_ITEM_ID`); writing it here would grant that unlock for free, so
this module never touches it.

Measured on the shipped tables (2026-11-21): 7,122 recipes; `59kRbpKz` (the EX-bonus gate) is "0" on
every one, and `F_UNIT_EX_BONUS_MST` has no kind-8 rows at all — so no shipped recipe is gated. The
gate is still enforced (a future table may add one); `check_exbonus_gate` is testable with a
synthetic row.
"""

import copy

import player_state
from agent import ops_basic, ops_items

FEATURE_KEY = "exvius_sublimation"
RECIPE_KEY = "SJ56uhz2"
BEFORE_KEY = "hxu12ZFS"
AFTER_KEY = "Q1n9iYgz"
UNITS_KEY = "25oxcKwN"
GATE_KEY = "59kRbpKz"
COST_KEY = "HS7V6Ww1"
GIL_KEY = "7UR4J2SE"


def recipes():
    return player_state._load_ndjson("F_SUBLIMATION_RECIPE_MST.ndjson", RECIPE_KEY)


def predecessors():
    """{awakened skill id: the skill it is awakened FROM}."""
    return {str(row.get(AFTER_KEY)): str(row.get(BEFORE_KEY)) for row in recipes().values()}


def tier_of(recipe_id, preds=None):
    """1 for a base skill, up to 5 walking back through the chain (the server's own measure)."""
    preds = preds if preds is not None else predecessors()
    row = recipes().get(str(recipe_id))
    if row is None:
        raise ValueError(f"unknown sublimation recipe {recipe_id!r}")
    tier, previous = 1, str(row.get(BEFORE_KEY))
    while previous in preds and tier < 5:
        previous = preds[previous]
        tier += 1
    return tier


def allowed_units(row):
    return [part for part in str(row.get(UNITS_KEY) or "").split(",") if part]


def unit_allowed(unit, row):
    master = player_state.unit_mst(unit["master_id"]) or {}
    allowed = allowed_units(row)
    return str(unit["master_id"]) in allowed or str(master.get("woghJa61")) in allowed


def list_recipes(unit_master_id):
    """Every recipe this unit (or its series root) may learn, with its chain tier."""
    master = player_state.unit_mst(unit_master_id) or {}
    root = str(master.get("woghJa61") or unit_master_id)
    preds = predecessors()
    out = []
    for recipe_id, row in recipes().items():
        allowed = allowed_units(row)
        if str(unit_master_id) not in allowed and root not in allowed:
            continue
        out.append({
            "recipe_id": str(recipe_id),
            "before": str(row.get(BEFORE_KEY)),
            "after": str(row.get(AFTER_KEY)),
            "tier": tier_of(recipe_id, preds),
            "materials": ops_items._parse_cost(row.get(COST_KEY)),
            "gil": int(row.get(GIL_KEY) or 0),
            "needs_exbonus": str(row.get(GATE_KEY) or "0") != "0",
        })
    out.sort(key=lambda item: (item["tier"], item["recipe_id"]))
    return out


def check_exbonus_gate(ps, instance_id, row, force=False):
    """The server's own gate: kind-8 EX-bonus rows naming this recipe, unlocked on this unit."""
    if str(row.get(GATE_KEY) or "0") == "0":
        return False
    if force:
        return True
    details = ((ps.data.get("exvius_exbonus") or {}).get("units") or {}).get(str(instance_id), {})
    acquired = details.get("details") or {}
    for bonus in player_state._load_ndjson("F_UNIT_EX_BONUS_MST.ndjson").get("__rows__", []):
        if str(bonus.get("xm7c08IS")) != "8":
            continue
        if str(bonus.get("xHZj7Va5")) != str(row.get(RECIPE_KEY)):
            continue
        if int(acquired.get(str(bonus.get("L3Nx1Jpb")), 0)) > 0:
            return False
    raise ValueError("the EX-bonus enhancement limit has not been unlocked for this unit "
                     "(pass force_exbonus to override)")


def apply_ability(ps, instance_id, recipe_id, grant_materials=False, force_exbonus=False):
    """Learn one awakening step. Returns what changed; writes only `skills[iid][root]`."""
    unit = ps.roster_row(instance_id)
    if unit is None:
        raise ValueError(f"unit {instance_id!r} is not owned")
    row = recipes().get(str(recipe_id))
    if row is None:
        raise ValueError(f"unknown sublimation recipe {recipe_id!r}")
    if not unit_allowed(unit, row):
        raise ValueError(f"recipe {recipe_id} is not allowed for unit {unit['master_id']}")

    forced = check_exbonus_gate(ps, str(instance_id), row, force=force_exbonus)

    state = copy.deepcopy(ps.data.get(FEATURE_KEY) or {})
    skills = state.setdefault("skills", {}).setdefault(str(instance_id), {})
    before, after = str(row.get(BEFORE_KEY)), str(row.get(AFTER_KEY))
    preds = predecessors()
    root = next((key for key, current in skills.items() if str(current) == before), before)
    if str(skills.get(root, "")) == after:
        return {"recipe_id": str(recipe_id), "root": root, "before": before, "after": after,
                "already": True, "forced_exbonus": forced}
    if not (root in skills or before not in preds):
        raise ValueError(f"the previous skill tier ({before}) has not been learned")
    if root in skills and str(skills[root]) != before:
        raise ValueError(f"skill recipe is stale: {root} is at {skills[root]}, the recipe expects {before}")

    granted = []
    if grant_materials:
        materials = ops_items._parse_cost(row.get(COST_KEY))
        # Recipe costs carry ids as strings (data-native); the edits schema takes integers.
        ops_items.apply_materials(ps, [{"item_id": int(item["item_id"]), "count": int(item["count"])}
                                       for item in materials])
        granted = materials

    skills[root] = after
    ps.data[FEATURE_KEY] = state
    ps.dirty = True
    return {"recipe_id": str(recipe_id), "root": root, "before": before, "after": after,
            "tier": tier_of(recipe_id, preds), "granted_materials": granted,
            "forced_exbonus": forced}
