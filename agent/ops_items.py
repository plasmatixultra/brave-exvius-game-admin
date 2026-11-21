"""Enhancement points, awakening materials, and recipe kits.

The XP-item mechanism, verified on the live save 2026-11-19: the server never puts King Metal
Minituar and friends in the roster — it credits POINTS into
`exvius_unit_points.banks["0"][<token>]`, and `UnitUsePointRequest` spends them with
`add_unit_exp` (spec "Left to the builder" 2). So the XP control adds points; the game spends them.

Materials are ordinary type-20 bag items and go through `PlayerState.grant_item`, which validates
nothing — caps and existence are checked here.
"""

import os

import player_state
from agent import ops_basic

RECIPE_TABLES = {
    # keyboard name -> (master table, cost column, key column, optional level column)
    "unit_class_up": ("F_UNIT_CLASS_UP_MST", "KCk8u0am", "3HriTp6B", None),
    "ex_class_up": ("F_NV_EX_CLASS_UP_MST", "KCk8u0am", "JPW8Vs40", "f8vk4JrD"),
    "sublimation": ("F_SUBLIMATION_RECIPE_MST", "HS7V6Ww1", "SJ56uhz2", None),
}
GIL_COL = "7UR4J2SE"
BANK_SERIES = "0"   # the global bank; per-series banks exist but no control writes them


def point_index(kind):
    """Index of a POINT_TYPES name, straight from the server's own tuple."""
    from exvius_actions_shops import POINT_TYPES
    try:
        return POINT_TYPES.index(kind)
    except ValueError:
        raise ValueError(f"unknown point type {kind!r}; known: {', '.join(POINT_TYPES)}") from None


def apply_points(ps, points):
    """Add enhancement points to the global bank. `points` is {POINT_TYPES name: integer}."""
    from exvius_actions_shops import POINT_TOKENS
    if not isinstance(points, dict):
        raise ValueError("points: expected an object of type -> integer")
    state = ps.data.setdefault("exvius_unit_points", {})
    banks = state.setdefault("banks", {})
    bank = banks.setdefault(BANK_SERIES, {})
    for kind, amount in points.items():
        index = point_index(kind)
        delta = ops_basic.as_int(amount, kind)
        if delta < 0:
            raise ValueError(f"{kind}: negative amounts are refused, got {delta}")
        token = POINT_TOKENS[index]
        bank[token] = int(bank.get(token, 0) or 0) + delta


def apply_materials(ps, rows):
    """Grant type-20 materials. `rows` is [{"item_id": int, "count": int}]."""
    if not isinstance(rows, list):
        raise ValueError("materials: expected a list of {item_id, count}")
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"materials: every row must be an object, got {row!r}")
        item_id = ops_basic.as_int(row.get("item_id"), "item_id")
        count = ops_basic.as_int(row.get("count"), "count")
        if count < 1:
            raise ValueError(f"materials: count must be at least 1, got {count}")
        cap = ops_basic.stack_cap(ops_basic.BAG_TYPE, item_id)
        if cap is None:
            raise ValueError(f"materials: unknown item {ops_basic.BAG_TYPE}/{item_id}")
        before = ops_basic.held(ps, ops_basic.BAG_TYPE, item_id)
        if before + count > cap:
            raise ValueError(f"materials: {item_id} {before}+{count} exceeds the stack cap {cap}")
        ps.grant_item(ops_basic.BAG_TYPE, item_id, count)


def _rows(table):
    return player_state._load_ndjson(table + ".ndjson").get("__rows__", [])


def _parse_cost(value):
    """`type:id:count` triples, comma separated. Every shipped recipe is type 20 (measured)."""
    out = []
    for token in str(value or "").split(","):
        parts = token.split(":")
        if len(parts) != 3:
            continue
        try:
            item_type, item_id, count = int(parts[0]), parts[1], int(parts[2])
        except ValueError:
            continue
        if item_type != ops_basic.BAG_TYPE:
            raise ValueError(f"recipe cost {token!r} is not a type-20 material")
        out.append({"item_id": item_id, "count": count})
    return out


def recipe_kit(table, row_id):
    """The exact materials + gil a recipe charges.

    `table` is `unit_class_up`, `ex_class_up` or `sublimation`. `row_id` is the table's own key;
    EX recipes are keyed by (group, level), so `"100031607"` means EX+1 (the lowest level) and
    `"100031607:2"` picks EX+2 explicitly.
    """
    if table not in RECIPE_TABLES:
        raise ValueError(f"unknown recipe table {table!r}; known: {', '.join(RECIPE_TABLES)}")
    master, cost_col, key_col, level_col = RECIPE_TABLES[table]
    row_id = str(row_id)
    wanted_level = None
    if level_col and ":" in row_id:
        row_id, _, level_text = row_id.partition(":")
        try:
            wanted_level = int(level_text)
        except ValueError:
            raise ValueError(f"recipe level {level_text!r} is not an integer") from None

    matches = [row for row in _rows(master) if str(row.get(key_col)) == row_id]
    if level_col:
        matches.sort(key=lambda row: int(row.get(level_col) or 0))
        if wanted_level is not None:
            matches = [row for row in matches if int(row.get(level_col) or 0) == wanted_level]
    if not matches:
        raise ValueError(f"no {table} recipe for {row_id}"
                         + (f" at level {wanted_level}" if wanted_level is not None else ""))
    row = matches[0]
    return {"materials": _parse_cost(row.get(cost_col)),
            "gil": int(row.get(GIL_COL) or 0),
            "row_id": row_id,
            "level": int(row.get(level_col) or 0) if level_col else None,
            "table": table}


def wiki_materials_path():
    """`data/wiki_materials.txt` beside the package (used by the catalog, kept here for callers)."""
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "wiki_materials.txt")
