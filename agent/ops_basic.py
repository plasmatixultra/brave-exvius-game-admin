"""Currency and ticket edits on a loaded `PlayerState`.

Currencies are written straight into `data["currencies"]` on purpose: the server's own
`add_currency` scores daily quests and writes the 400-entry quest audit ring, which a grant
must not do (spec "Left to the builder" 2, verified against the live save).

Tickets go through `PlayerState.grant_item` (find-or-append on `inventory.items`), which does
no validation of its own — every cap and existence check below is the agent's.
"""

import player_state

CURRENCY_KEYS = ("gil", "lapis_free", "lapis_paid")
BAG_TYPE = 20            # F_ITEM_MST, served to the phone as the regular item bag
IMPORTANT_TYPE = 23      # F_IMPORTANT_ITEM_MST, what the summon banners read for held tickets
CAP_COL = "9J6uRe8f"     # stack cap in both item tables
IMPORTANT_ID_COL = "2dfixko3"

# Type-23 ids granted by login bonuses that no banner spends; handing them out would be a dud.
UNSPENDABLE = {
    "908": "Premium 10+1 Summon Ticket",
    "909": "5★ Guaranteed 10+1 Summon Ticket",
}


def item_master(item_type, item_id):
    """The master row for a type-20 or type-23 item, or None when the id is not in that table."""
    try:
        item_type = int(item_type)
    except (TypeError, ValueError):
        return None
    if item_type == IMPORTANT_TYPE:
        return player_state._load_ndjson("F_IMPORTANT_ITEM_MST.ndjson", IMPORTANT_ID_COL).get(str(item_id))
    if item_type == BAG_TYPE:
        return player_state._load_ndjson("F_ITEM_MST.ndjson", player_state.ITEM_KEY).get(str(item_id))
    return None


def stack_cap(item_type, item_id):
    """The row's own stack cap, or None when the item is unknown or carries no cap."""
    row = item_master(item_type, item_id)
    if row is None:
        return None
    try:
        return int(row.get(CAP_COL) or 0) or None
    except (TypeError, ValueError):
        return None


def held(ps, item_type, item_id):
    for row in ps.data.get("inventory", {}).get("items") or []:
        if str(row.get("item_type")) == str(item_type) and str(row.get("item_id")) == str(item_id):
            return int(row.get("count", 0))
    return 0


def as_int(value, label):
    """A JSON integer, refusing bools, numeric strings and floats. Edit payloads are strict:
    the app builds them, so a string is a bug, not a convenience to absorb."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label}: expected an integer, got {value!r}")
    return value


def apply_currencies(ps, edits):
    """Add the requested amounts. Returns {key: {"before", "after"}}."""
    rows = (edits or {}).get("currencies") or {}
    if not isinstance(rows, dict):
        raise ValueError("currencies: expected an object of name -> integer")
    out = {}
    for key, value in rows.items():
        if key not in CURRENCY_KEYS:
            raise ValueError(f"unknown currency {key!r}; known: {', '.join(CURRENCY_KEYS)}")
        delta = as_int(value, key)
        if delta < 0:
            raise ValueError(f"{key}: negative amounts are refused, got {delta}")
        currencies = ps.data.setdefault("currencies", {})
        before = int(currencies.get(key, 0) or 0)
        currencies[key] = before + delta
        out[key] = {"before": before, "after": currencies[key]}
    return out


def apply_tickets(ps, rows):
    """Grant ticket/item rows. Returns a list of {item_type, item_id, before, after}."""
    if not isinstance(rows, list):
        raise ValueError("tickets: expected a list of {item_type, item_id, count}")
    out = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"tickets: every row must be an object, got {row!r}")
        try:
            item_type = as_int(row["item_type"], "item_type")
            item_id = as_int(row["item_id"], "item_id")
            count = as_int(row["count"], "count")
        except KeyError as exc:
            raise ValueError(f"tickets: row is missing {exc.args[0]!r}") from None
        if count < 1:
            raise ValueError(f"tickets: count must be at least 1, got {count}")
        if item_type not in (BAG_TYPE, IMPORTANT_TYPE):
            raise ValueError(f"tickets: item_type {item_type} is not supported (only 20 and 23)")
        if item_type == IMPORTANT_TYPE and str(item_id) in UNSPENDABLE:
            raise ValueError(f"tickets: {item_id} is unspendable "
                             f"({UNSPENDABLE[str(item_id)]} — no banner spends it)")
        cap = stack_cap(item_type, item_id)
        if cap is None:
            raise ValueError(f"tickets: unknown item {item_type}/{item_id}")
        before = held(ps, item_type, item_id)
        if before + count > cap:
            raise ValueError(f"tickets: {item_type}/{item_id} {before}+{count} exceeds the stack cap {cap}")
        after = ps.grant_item(item_type, item_id, count)
        out.append({"item_type": item_type, "item_id": item_id, "before": before, "after": after})
    return out
