"""The catalog: everything the page's pickers need, built from the shipped tables.

Pure over the files it is given — no `player_state` import, no environment, so it can be tested
as a plain function and cached freely by the web app.

    build(data_dir, server_dir) -> {"tickets", "units", "awaken_chain", "vision_cards",
                                    "materials", "points", "xp_units"}

`data_dir` is the shipped `static/` directory (the one holding `mst_decoded/` and `text_decoded/`).
`server_dir` holds the banner files (`gacha_named.json`, `gacha_deals.json`).

Facts measured on the shipped tables (2026-11-21, recorded here because later tasks quote them):
- F_TICKET_MST = 17 type-23 single tickets, ids 50/70/80/160/170/180/210-250/260/10006/10007/110160-110180.
- 10+1 tickets are banner COSTS, not F_TICKET_MST rows: 20/1209001623 (9200019), 20/1300000007 (9200020),
  20/1209002123 (9200017), each `draws: 11`. 908/909 are type-23 and unspendable (no banner spends them).
- F_UNIT_CLASS_UP_MST: 1,728 rows; Lightning 213000105 -> 106 -> 107 -> 117.
- F_SUBLIMATION_RECIPE_MST: 7,122 rows, 88 distinct materials (40 cryst ids 2700xxxxx, 18 forticite 2710xxxxx).
- `data/wiki_materials.txt` (236 names): 233 resolve to a type-20 item. The 3 that do not are wiki-only
  umbrella names — "Lid & Jake Fragment", "Master's Crown (Unit-specific)", "Unit's Prism".
"""

import json
import os

FAMILY_BY_SUFFIX = (("fragment", "fragment"), ("forticite", "forticite"),
                    ("crystal", "crystal"), ("cryst", "crystal"), ("pearl", "pearl"),
                    ("core", "core"), ("writ", "writ"), ("bloom", "bloom"),
                    ("gem", "gem"), ("horn", "horn"), ("tear", "tear"),
                    ("insignia", "insignia"))

UNSPENDABLE_TICKETS = {
    908: ("Premium 10+1 Summon Ticket", 11),
    909: ("5★ Guaranteed 10+1 Summon Ticket", 11),
}


def build_points():
    """The enhancement-point bank tokens, straight from the server (exvius_actions_shops.py:824).

    Read from the module rather than copied: an invented list would silently address the wrong
    bank. The pairs are positional, so the two tuples must stay the same length.
    """
    from exvius_actions_shops import POINT_TOKENS, POINT_TYPES
    return [{"token": token, "type": kind} for token, kind in zip(POINT_TOKENS, POINT_TYPES)]


def _rows(mst_dir, table):
    path = os.path.join(mst_dir, table + ".ndjson")
    try:
        with open(path, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]
    except OSError:
        return []


def _texts(text_dir, table, prefix):
    """{suffix of `key`: first localisation} — the English string is `v[0]`."""
    out = {}
    path = os.path.join(text_dir, table + ".ndjson")
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                key = str(row.get("key", ""))
                if key.startswith(prefix):
                    values = row.get("v") or []
                    out[key[len(prefix):]] = str(values[0]) if values else ""
    except (OSError, ValueError):
        return {}
    return out


def _cost(value):
    """Parse a `type:id:count` cost string (comma separated) into [(type, id, count)]."""
    out = []
    for token in str(value or "").split(","):
        parts = token.split(":")
        if len(parts) == 3:
            try:
                out.append((int(parts[0]), str(parts[1]), int(parts[2])))
            except ValueError:
                continue
    return out


def _int(value, fallback=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def material_family(item_id, name):
    """A grouping label the UI can colour by. The two id ranges that ARE a family win over the
    name: `2700xxxxx` is the cryst ladder and `2710xxxxx` is forticite, both measured."""
    if item_id.startswith("2700"):
        return "cryst"
    if item_id.startswith("2710"):
        return "forticite"
    lowered = (name or "").lower()
    for suffix, family in FAMILY_BY_SUFFIX:
        if lowered.endswith(suffix):
            return family
    return "other"


def _banners(server_dir):
    for filename in ("gacha_named.json", "gacha_deals.json"):
        path = os.path.join(server_dir, filename)
        try:
            with open(path, encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, ValueError):
            continue
        banners = payload.get("banners") or []
        if isinstance(banners, dict):
            banners = list(banners.values())
        for banner in banners:
            yield banner


def build_tickets(mst_dir, text_dir, server_dir):
    important_names = _texts(text_dir, "F_TEXT_IMPORTANT_ITEM_NAME", "MST_IMPORTANT_ITEM_NAME_")
    item_names = _texts(text_dir, "F_TEXT_ITEM_NAME", "MST_ITEM_NAME_")
    tickets = []
    seen = set()

    for row in _rows(mst_dir, "F_TICKET_MST"):
        item_type = _int(row.get("89EvGKHx"), 23)
        item_id = _int(row.get("tL6G9egd"))
        if not item_id or (item_type, item_id) in seen:
            continue
        seen.add((item_type, item_id))
        tickets.append({
            "item_type": item_type,
            "item_id": item_id,
            "name": important_names.get(str(item_id)) or item_names.get(str(item_id)) or str(item_id),
            "pulls": 1,
            "spendable": True,
            "note": "single summon ticket",
        })

    for banner in _banners(server_dir):
        draws = _int(banner.get("draws"), 1)
        cost = banner.get("cost") or {}
        kind = str(cost.get("kind") or "")
        if kind not in ("item", "important") or draws <= 1:
            continue    # single-summon banners are F_TICKET_MST ids, not 10+1 tickets
        item_id = _int(cost.get("item_id"))
        item_type = 23 if kind == "important" else 20
        if not item_id or (item_type, item_id) in seen:
            continue
        seen.add((item_type, item_id))
        name = (important_names if item_type == 23 else item_names).get(str(item_id))
        if not name:
            continue    # unnamed banner costs are coins/exchange items, not summon tickets
        tickets.append({
            "item_type": item_type,
            "item_id": item_id,
            "name": name,
            "pulls": draws,
            "spendable": True,
            "note": f"{draws} pulls · banner {banner.get('id')} {banner.get('name') or ''}".strip(),
        })

    for item_id, (name, pulls) in UNSPENDABLE_TICKETS.items():
        if (23, item_id) in seen:
            continue
        seen.add((23, item_id))
        tickets.append({
            "item_type": 23,
            "item_id": item_id,
            "name": name,
            "pulls": pulls,
            "spendable": False,
            "note": "unspendable — no banner spends it (login bonus only)",
        })

    tickets.sort(key=lambda row: (row["item_type"], row["item_id"]))
    return tickets


def build_units(mst_dir, text_dir):
    names = _texts(text_dir, "F_TEXT_UNITS_NAME", "MST_UNIT_NAME_")
    units = []
    for row in _rows(mst_dir, "F_UNIT_MST"):
        master_id = str(row.get("3HriTp6B") or "")
        if not master_id:
            continue
        name = names.get(master_id)
        if not name:
            # A granted unit with no MST_UNIT_NAME_ row SIGSEGVs the client's award screen (SW-78).
            continue
        units.append({
            "master_id": master_id,
            "name": name,
            "rarity": _int(row.get("9fW0TePj"), 1),
            "nv_group": str(row.get("woghJa61") or master_id),
            "nv": str(row.get("JPW8Vs40") or "0") == master_id,
        })
    units.sort(key=lambda row: row["master_id"])
    return units


def build_awaken_chain(mst_dir):
    forward = {}
    for row in _rows(mst_dir, "F_UNIT_CLASS_UP_MST"):
        source = str(row.get("3HriTp6B") or "")
        target = str(row.get("t74crqAL") or "")
        if source and target and target != "0":
            forward.setdefault(source, []).append(target)
    chains = {}
    for source in forward:
        chain, seen, current = [], {source}, source
        while forward.get(current):
            nxt = forward[current][0]
            if nxt in seen:
                break
            chain.append(nxt)
            seen.add(nxt)
            current = nxt
        chains[source] = chain
    return chains


def build_vision_cards(mst_dir, text_dir):
    names = _texts(text_dir, "F_TEXT_VISION_CARD_NAME", "MST_VISION_CARD_NAME_")
    cards = []
    for row in _rows(mst_dir, "F_VISION_CARD_MST"):
        vc_id = str(row.get("5giCMUd2") or "")
        if not vc_id:
            continue
        cards.append({
            "vc_id": vc_id,
            "name": names.get(vc_id) or str(row.get("G4L0YIB2") or vc_id),
            "max_level": _int(row.get("aU3o2D4t"), 1),
        })
    cards.sort(key=lambda row: row["vc_id"])
    return cards


def build_materials(mst_dir, text_dir, wiki_path=None):
    names = _texts(text_dir, "F_TEXT_ITEM_NAME", "MST_ITEM_NAME_")
    bag_ids = {str(row.get("tL6G9egd")) for row in _rows(mst_dir, "F_ITEM_MST")}
    wanted = set()

    # Everything the three recipe tables charge.
    for table, column in (("F_UNIT_CLASS_UP_MST", "KCk8u0am"),
                          ("F_NV_EX_CLASS_UP_MST", "KCk8u0am"),
                          ("F_SUBLIMATION_RECIPE_MST", "HS7V6Ww1")):
        for row in _rows(mst_dir, table):
            for _kind, item_id, _count in _cost(row.get(column)):
                wanted.add(item_id)

    # The wiki category list, as a UI grouping aid (it names nothing the data lacks, and vice versa).
    if wiki_path and os.path.exists(wiki_path):
        by_name = {}
        for item_id, name in names.items():
            by_name.setdefault(name.strip().lower(), item_id)
        with open(wiki_path, encoding="utf-8") as fh:
            for line in fh:
                name = line.strip()
                if not name:
                    continue
                item_id = by_name.get(name.lower())
                if item_id:
                    wanted.add(item_id)

    materials = []
    for item_id in sorted(wanted):
        if item_id not in bag_ids:
            continue
        name = names.get(item_id) or item_id
        materials.append({"item_id": item_id, "name": name,
                          "family": material_family(item_id, name)})
    materials.sort(key=lambda row: (row["family"], row["name"], row["item_id"]))
    return materials


def build_xp_units(mst_dir, text_dir):
    names = _texts(text_dir, "F_TEXT_UNITS_NAME", "MST_UNIT_NAME_")
    out = []
    for row in _rows(mst_dir, "F_UNIT_MST"):
        master_id = str(row.get("3HriTp6B") or "")
        if master_id.startswith("9") and master_id in names:
            out.append({"master_id": master_id, "name": names[master_id]})
    out.sort(key=lambda row: row["master_id"])
    return out


def build(data_dir, server_dir=None, wiki_path=None):
    """Build the whole catalog. `data_dir` is the shipped `static/` directory."""
    if os.path.basename(data_dir.rstrip("/")) == "mst_decoded":
        data_dir = os.path.dirname(data_dir.rstrip("/"))
    mst_dir = os.path.join(data_dir, "mst_decoded")
    text_dir = os.path.join(data_dir, "text_decoded")
    server_dir = server_dir or ""
    if wiki_path is None:
        wiki_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 "data", "wiki_materials.txt")
    return {
        "tickets": build_tickets(mst_dir, text_dir, server_dir),
        "units": build_units(mst_dir, text_dir),
        "awaken_chain": build_awaken_chain(mst_dir),
        "vision_cards": build_vision_cards(mst_dir, text_dir),
        "materials": build_materials(mst_dir, text_dir, wiki_path),
        "points": build_points(),
        "xp_units": build_xp_units(mst_dir, text_dir),
    }
