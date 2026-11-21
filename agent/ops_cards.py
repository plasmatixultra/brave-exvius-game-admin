"""Vision Cards: grant one, and set an existing card's level.

A card's progress is `(level, exp, total_exp)`; `_card_progress` (exvius_actions_shops.py:1159)
requires `total_exp == threshold + exp`, where `threshold` is the sum of the per-level steps for
levels 2..level, and `exp == 0` at the cap. Writing `exp: 0` with the exact threshold satisfies it
(a card holding only `level` is read as exp 0, but the game then shows a wrong EXP bar).
"""

import time

import player_state

LEVEL_COL = "7wV3QZ80"
STEP_COL = "93fnYUJG"
PATTERN_COL = "0W7g6YXS"
CAP_COL = "aU3o2D4t"


def _steps(pattern_id):
    """{level: per-level step} for one EXP pattern. Each row's `93fnYUJG` is a STEP, not a total."""
    out = {}
    for row in player_state._load_ndjson("F_VISION_CARD_EXP_PATTERN_MST.ndjson").get("__rows__", []):
        if str(row.get(PATTERN_COL)) != str(pattern_id):
            continue
        try:
            out[int(row.get(LEVEL_COL))] = int(row.get(STEP_COL))
        except (TypeError, ValueError):
            continue
    return out


def card_master(vc_id):
    master = player_state.vision_card_mst(vc_id)
    if master is None:
        raise ValueError(f"unknown vision card {vc_id!r} (not in F_VISION_CARD_MST)")
    return master


def card_cap(master):
    return int(master.get(CAP_COL) or 1)


def card_total_exp(vc_id, level):
    """The cumulative EXP a card at `level` must carry: sum of the steps for levels 2..level."""
    master = card_master(vc_id)
    cap = card_cap(master)
    level = int(level)
    if not 1 <= level <= cap:
        raise ValueError(f"vision card {vc_id}: level {level} outside 1..{cap}")
    steps = _steps(master.get(PATTERN_COL))
    missing = [item for item in range(2, level + 1) if item not in steps]
    if missing:
        raise ValueError(f"vision card {vc_id}: EXP pattern is missing level(s) {missing}")
    return sum(steps[item] for item in range(2, level + 1))


def check_card(card, master):
    """Run the server's own progress check; raises ValueError when the row would be rejected."""
    from exvius_actions_shops import _card_progress
    return _card_progress(card, master)


def set_card_level(ps, user_vc_id, level):
    """Level an EXISTING card in place."""
    cards = ps.data.setdefault("vision_cards", [])
    card = next((row for row in cards if str(row.get("user_vc_id")) == str(user_vc_id)), None)
    if card is None:
        raise ValueError(f"vision card {user_vc_id!r} is not owned")
    master = card_master(card.get("vc_id"))
    total = card_total_exp(card.get("vc_id"), level)
    card["level"] = int(level)
    card["exp"] = 0
    card["total_exp"] = total
    check_card(card, master)


def next_card_id(cards):
    """A unique epoch-ms id, bumped past any collision — the same rule `grant_unit` uses."""
    occupied = {str(row.get("user_vc_id")) for row in cards}
    candidate = int(time.time() * 1000)
    while str(candidate) in occupied:
        candidate += 1
    return str(candidate)


def apply_cards(ps, rows):
    """Grant cards at a level. Returns the new `user_vc_id`s."""
    if not isinstance(rows, list):
        raise ValueError("cards: expected a list of {vc_id, level}")
    cards = ps.data.setdefault("vision_cards", [])
    granted = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"cards: every row must be an object, got {row!r}")
        vc_id = str(row.get("vc_id") or "")
        if not vc_id:
            raise ValueError("cards: vc_id is required")
        level = row.get("level", 1)
        if isinstance(level, bool) or not isinstance(level, int):
            raise ValueError(f"cards: level must be an integer, got {level!r}")
        master = card_master(vc_id)
        total = card_total_exp(vc_id, level)
        user_vc_id = next_card_id(cards)
        card = {"user_vc_id": user_vc_id, "vc_id": vc_id, "level": level, "exp": 0,
                "total_exp": total}
        check_card(card, master)
        cards.append(card)
        granted.append(user_vc_id)
    return granted
