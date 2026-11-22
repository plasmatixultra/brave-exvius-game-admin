"""The unit builder: grant a unit, or rebuild an owned one, at a chosen form/level/ex/LB/pots/trust.

A roster row is only half the story. `master_id` alone does not pay an awakening's rewards, so the
spec carries two explicit options — `grant_trust_reward` and `grant_ex_rewards` — that run the
server's OWN code paths (`gain_trust`, `_rewards` + `_grant`) and are paid once, exactly as the game
pays them.

Rules copied from the server and the verified data (2026-11-21):
- level 1..`max_level(master)`; `set_unit_level` clamps, this refuses.
- `lb_level` 1..min(highest `F_LIMITBURST_LV_MST.7wV3QZ80` for the unit's `PR2HIc71` id, `ENwn42DV`).
  Level 0 is a coherence error and crashes the client when the LB fires (player_state.py:1778).
- `nv_ex_level` 0..3, and only on the NV form itself (`JPW8Vs40 == master_id`). The plan's
  `JPW8Vs40 != "0"` is true for every Lightning form, so it cannot be the test; the self-reference is
  what `unit_ex_stats` and the client mean by "this row is the NV row".
- Super Trust needs rarity >= 7 (906 moogles exempt from the rarity rule).
- Trust / Super Trust caps are descriptor field 4 of `9mu4boy7` / `C74EmZ1I`.
- Pots/doors: `doors[i] <= cap[3]` and `pots[i] <= cap[2] + doors[i]` (the rule `unit_use_point` uses).
- A unit with no `MST_UNIT_NAME_<id>` row is refused: granting one SIGSEGVs the client's award
  screen (SW-78).
"""

import player_state

STAT_TOKENS = player_state.STAT_TOKENS
TRUST_COLUMNS = ("9mu4boy7", "C74EmZ1I")   # channel 0 = trust, 1 = super trust


# --------------------------------------------------------------------------- master lookups


def _rows(table):
    return player_state._load_ndjson(table + ".ndjson").get("__rows__", [])


_LB_LEVELS = None
_CHAINS = None


def lb_levels():
    global _LB_LEVELS
    if _LB_LEVELS is None:
        out = {}
        for row in _rows("F_LIMITBURST_LV_MST"):
            try:
                out.setdefault(str(row.get("PR2HIc71")), []).append(int(row.get("7wV3QZ80")))
            except (TypeError, ValueError):
                continue
        _LB_LEVELS = out
    return _LB_LEVELS


def lb_cap(master_id):
    """The highest legal `lb_level`, or None when the unit owns no Limit Burst."""
    burst = player_state.limit_burst(master_id)
    if burst is None:
        return None
    master = player_state.unit_mst(master_id) or {}
    hard = int(master.get("ENwn42DV") or 1) or 1
    levels = lb_levels().get(str(burst.get("PR2HIc71"))) or []
    return min(max(levels), hard) if levels else hard


def awaken_chain(master_id):
    """The forms this master upgrades into, in order (e.g. Lightning 105 -> 106 -> 107 -> 117)."""
    global _CHAINS
    if _CHAINS is None:
        forward = {}
        for row in _rows("F_UNIT_CLASS_UP_MST"):
            source = str(row.get("3HriTp6B") or "")
            target = str(row.get("t74crqAL") or "")
            if source and target and target != "0":
                forward.setdefault(source, [target])
        _CHAINS = forward
    chain, current, seen = [], str(master_id), {str(master_id)}
    while _CHAINS.get(current):
        nxt = _CHAINS[current][0]
        if nxt in seen:
            break
        chain.append(nxt)
        seen.add(nxt)
        current = nxt
    return chain


def is_nv_form(master_id, master):
    return str(master.get("JPW8Vs40") or "0") == str(master_id)


def descriptor_required(master, channel):
    """Field 4 of a trust descriptor, or None when the unit has no reward on that channel."""
    parts = str(master.get(TRUST_COLUMNS[channel]) or "").split(":")
    if len(parts) < 4 or not parts[3].strip().isdigit():
        return None
    return int(parts[3])


def descriptor_reward(master, channel):
    """`(kind, id, count, required)` from the descriptor, or None."""
    parts = str(master.get(TRUST_COLUMNS[channel]) or "").split(":")
    if len(parts) < 3 or not all(part.isdigit() for part in parts[:3]):
        return None
    required = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
    return int(parts[0]), int(parts[1]), int(parts[2]), required


def stat_caps(master):
    """[(pot_cap, door_cap)] for the six stats."""
    out = []
    for token in STAT_TOKENS:
        values = []
        for part in str(master.get(token) or "").split(","):
            try:
                values.append(int(part))
            except ValueError:
                break
        values += [0] * (4 - len(values))
        out.append((values[2], values[3]))
    return out


# --------------------------------------------------------------------------- spec


def _int(value, label, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label}: expected an integer, got {value!r}")
    if minimum is not None and value < minimum:
        raise ValueError(f"{label}: {value} is below {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{label}: {value} is above {maximum}")
    return value


def _six(value, label):
    if value is None:
        return [0] * 6
    if not isinstance(value, list) or len(value) != 6:
        raise ValueError(f"{label}: expected six integers")
    return [_int(item, f"{label}[{index}]", 0) for index, item in enumerate(value)]


def normalise_spec(spec):
    """Validate a UnitSpec and fill its defaults. Raises ValueError with a usable message."""
    if not isinstance(spec, dict):
        raise ValueError("unit spec: expected an object")
    master_id = str(spec.get("master_id") or "")
    master = player_state.unit_mst(master_id)
    if master is None:
        raise ValueError(f"unit spec: {master_id!r} is not an F_UNIT_MST id")
    if master_id not in player_state.named_unit_ids():
        raise ValueError(f"unit spec: {master_id} has no MST_UNIT_NAME_ row; granting it crashes "
                         "the client's award screen (SW-78)")

    cap = player_state.max_level(master_id)
    if not cap or cap <= 0:
        raise ValueError(f"unit spec: {master_id} has no level cap in the master data")
    level = _int(spec.get("level", 1), "level", 1, cap)

    burst_cap = lb_cap(master_id)
    lb_level = spec.get("lb_level", 1 if burst_cap else 0)
    if burst_cap is None:
        lb_level = _int(lb_level if lb_level is not None else 0, "lb_level", 0, 0)
    else:
        lb_level = _int(lb_level, "lb_level", 1, burst_cap)

    ex_level = _int(spec.get("nv_ex_level", 0), "nv_ex_level", 0, 3)
    if ex_level and not is_nv_form(master_id, master):
        raise ValueError(f"unit spec: {master_id} is not a Neo Vision form; EX awakening goes up to 3 "
                         "only on the NV row")

    pots = _six(spec.get("stat_pots"), "stat_pots")
    doors = _six(spec.get("stat_doors"), "stat_doors")
    for index, (pot_cap, door_cap) in enumerate(stat_caps(master)):
        if doors[index] > door_cap:
            raise ValueError(f"stat_doors[{index}]: {doors[index]} is above the cap {door_cap}")
        if pots[index] > pot_cap + doors[index]:
            raise ValueError(f"stat_pots[{index}]: {pots[index]} is above {pot_cap}+{doors[index]}")

    rarity = int(master.get("9fW0TePj") or 0)
    trust_cap = descriptor_required(master, 0) or 0
    super_cap = descriptor_required(master, 1) or 0
    trust = _int(spec.get("trust", 0), "trust", 0, trust_cap)
    super_enabled = rarity >= 7 or master_id.startswith("906")
    super_trust = _int(spec.get("super_trust", 0), "super_trust", 0, super_cap)
    if super_trust and not super_enabled:
        raise ValueError(f"unit spec: {master_id} is {rarity}★; Super Trust needs 7★ (906 moogles "
                         "exempt)")

    grant_trust_reward = bool(spec.get("grant_trust_reward"))
    if grant_trust_reward:
        for channel, value in ((0, trust), (1, super_trust)):
            if not value:
                continue
            required = descriptor_required(master, channel)
            if required is None or value != required:
                label = "trust" if channel == 0 else "super trust"
                raise ValueError(f"unit spec: granting the {label} reward needs the value at its cap "
                                 f"({required if required is not None else 'none'}), got {value}")

    return {
        "master_id": master_id,
        "level": level,
        "nv_ex_level": ex_level,
        "lb_level": lb_level,
        "stat_pots": pots,
        "stat_doors": doors,
        "trust": trust,
        "super_trust": super_trust,
        "grant_trust_reward": grant_trust_reward,
        "grant_ex_rewards": bool(spec.get("grant_ex_rewards")),
    }


def preview_unit(spec):
    """What the game would show for this spec, computed with the server's own `owned_unit_stats`."""
    clean = normalise_spec(spec)
    master_id = clean["master_id"]
    master = player_state.unit_mst(master_id) or {}
    row = {
        "master_id": master_id,
        "level": clean["level"],
        "exp": 0,
        "nv_ex_level": clean["nv_ex_level"],
        "stat_pots": clean["stat_pots"],
        "stat_doors": clean["stat_doors"],
        "exbonus_stats": [0] * 6,
        "affinity_points": [str(clean["trust"]), str(clean["super_trust"])],
    }
    return {
        "stats": player_state.owned_unit_stats(row),
        "max_level": player_state.max_level(master_id),
        "lb_cap": lb_cap(master_id),
        "trust_cap": descriptor_required(master, 0) or 0,
        "super_trust_cap": descriptor_required(master, 1) or 0,
        "super_trust_enabled": int(master.get("9fW0TePj") or 0) >= 7 or master_id.startswith("906"),
        "awaken_chain": awaken_chain(master_id),
        "stat_caps": [list(pair) for pair in stat_caps(master)],
        "rarity": int(master.get("9fW0TePj") or 0),
        "name": master_id,
    }


# --------------------------------------------------------------------------- rewards


def _apply_trust(ps, unit, spec):
    """Write the points, then let the server pay the mastery reward when it is at the cap."""
    unit["affinity_points"] = [str(spec["trust"]), str(spec["super_trust"])]
    if not spec["grant_trust_reward"]:
        return
    master = player_state.unit_mst(unit["master_id"]) or {}
    for channel, value in ((0, spec["trust"]), (1, spec["super_trust"])):
        if not value:
            continue
        required = descriptor_required(master, channel)
        if not required:
            continue
        unit["affinity_points"][channel] = str(required - 1)
        if ps.gain_trust(unit["instance_id"], 1, channel) is None:
            raise ValueError(f"the server refused the {'trust' if channel == 0 else 'super trust'} "
                             "mastery reward")
        if int(unit["affinity_points"][channel]) != required:
            raise ValueError(f"{'trust' if channel == 0 else 'super trust'} reward could not reach "
                             f"{required} (the row would lose it); nothing was saved")


def ex_reward_rows(master, ex_level):
    """The `dX6cor8j` rewards for EX levels 1..ex_level, parsed exactly as `unit_ex_class_up` does."""
    group = str(master.get("JPW8Vs40") or "0")
    steps = [row for row in _rows("F_NV_EX_CLASS_UP_MST")
             if str(row.get("JPW8Vs40")) == group and int(row.get("f8vk4JrD") or 0) <= ex_level]
    steps.sort(key=lambda row: int(row.get("f8vk4JrD") or 0))
    rewards = []
    for step in steps:
        for entry in str(step.get("dX6cor8j") or "").split(","):
            if not entry:
                continue
            fields = [part.strip() for part in entry.split(":")]
            if len(fields) not in (3, 4, 5, 9):
                raise ValueError(f"invalid EX awakening reward {entry!r}")
            if len(fields) >= 4 and fields[3] != "100":
                raise ValueError(f"invalid EX awakening reward {entry!r}")
            reward = {"kind": int(fields[0]), "id": int(fields[1]), "count": int(fields[2])}
            if len(fields) >= 5:
                reward["level"] = int(fields[4] or 1)
            if len(fields) == 9:
                if fields[5:8] != ["0", "0", "0"] or reward["kind"] != 10:
                    raise ValueError(f"invalid EX trust reward {entry!r}")
                target = int(fields[8])
                channel = 1 if target >= 1000000000 else 0
                target_master = player_state.unit_mst(target - channel * 1000000000) or master
                descriptor = (target_master or master).get(TRUST_COLUMNS[channel])
                if not descriptor or not str(reward["id"]).startswith(("904", "906")):
                    raise ValueError(f"missing EX trust reward target in {entry!r}")
                reward["affinity_info"] = ["", ""]
                reward["affinity_points"] = ["", ""]
                reward["affinity_info"][channel] = descriptor
                reward["affinity_points"][channel] = descriptor.split(":")[3]
            rewards.append(reward)
    return rewards


def grant_ex_rewards(ps, unit, ex_level):
    """Pay the EX awakening rewards for levels 1..ex_level through the server's own `_rewards`+`_grant`."""
    import ffbe_state
    from exvius_actions_shops import _grant, _rewards
    master = player_state.unit_mst(unit["master_id"]) or {}
    rewards = ex_reward_rows(master, ex_level)
    if not rewards:
        return []
    ffbe_state.State.player = ps     # `_grant` acts on the global player; without it cards lose their
    _rewards(rewards)                # exp fields and kind-50 rows get the purchase multiplier
    _grant(rewards)
    return rewards


def expected_roster_added(edits):
    """Roster rows the `units` group will add: the built units plus any unit rewards they trigger."""
    added = 0
    for row in edits.get("units") or []:
        if not isinstance(row, dict):
            continue
        if row.get("instance_id") in (None, ""):
            added += 1
        spec = row.get("spec") or {}
        master_id = str(spec.get("master_id") or "")
        master = player_state.unit_mst(master_id) or {}
        if spec.get("grant_ex_rewards"):
            ex_level = int(spec.get("nv_ex_level") or 0)
            if ex_level:
                for reward in ex_reward_rows(master, ex_level):
                    if reward["kind"] == 10:
                        added += reward["count"]
        if spec.get("grant_trust_reward"):
            for channel, key in ((0, "trust"), (1, "super_trust")):
                if not int(spec.get(key) or 0):
                    continue
                reward = descriptor_reward(master, channel)
                if reward and reward[0] == 10:
                    added += reward[2]
    return added


# --------------------------------------------------------------------------- build


def build_unit(ps, spec, instance_id=None):
    """Create (instance_id None) or rebuild one roster row. Returns the resulting row."""
    clean = normalise_spec(spec)
    master_id = clean["master_id"]
    if instance_id in (None, ""):
        unit = ps.grant_unit(master_id)
        if unit is None:
            raise ValueError(f"the server refused to grant {master_id}")
    else:
        unit = ps.roster_row(instance_id)
        if unit is None:
            raise ValueError(f"unit {instance_id!r} is not owned")
        if str(unit.get("master_id")) != master_id:
            # A form change is an awakening without the cost: mirror `_awaken_unit` — move the master id,
            # re-level, clamp the LB, then renew the instance id (the client keys forms by instance).
            unit["master_id"] = master_id
            ps.set_unit_level(unit["instance_id"], clean["level"])
            burst_cap = lb_cap(master_id)
            if burst_cap is not None:
                unit["lb_level"] = min(int(unit.get("lb_level") or 1), burst_cap)
            renewed = ps.renew_unit_instance(unit["instance_id"])
            if renewed:
                unit = renewed

    ps.set_unit_level(unit["instance_id"], clean["level"])
    unit = ps.roster_row(unit["instance_id"]) or unit
    unit["exp"] = 0
    unit["nv_ex_level"] = clean["nv_ex_level"]
    unit["lb_exp"] = 0
    unit["lb_level"] = clean["lb_level"]
    unit["stat_pots"] = list(clean["stat_pots"])
    unit["stat_doors"] = list(clean["stat_doors"])
    _apply_trust(ps, unit, clean)
    if clean["grant_ex_rewards"] and clean["nv_ex_level"]:
        grant_ex_rewards(ps, unit, clean["nv_ex_level"])
    ps.dirty = True
    return unit
