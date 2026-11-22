"""Replay a story checkpoint or a side-content selection inside the agent's apply session.

The plan comes from `agent.story` (pure catalog). This module does the writing: the pipeline's own call
order per mission, the gate switches, quest completion, the esper toggles — and then it proves what it
did against what it promised.

    preview(ps, mst_dir, progress_edit) -> {"plan", "expected", "warnings"}
    apply(ps, mst_dir, progress_edit)   -> {"cleared", ..., "paid_lapis", "paid_gil"}

Safety rules, each measured in the spikes:
- attempt/first_clear.last/exvius_mission_gil are restored afterwards (a re-sent mission end pays nothing).
- gates are written only from master data, and only for areas the checkpoint passed.
- a run that pays LESS than its preview is a refusal, not a warning.
"""

import copy
import contextlib
import io
import json
import os
import time
from collections import Counter

import player_state
from agent import story

RESULT_PLAINTEXT = json.dumps({"2urzfX6d": [{"69ieJGhD": "1:1:1"}]})


class QuestlineError(ValueError):
    """A refusal: the plan cannot be run safely, or the run did not deliver its preview."""


# ---------------------------------------------------------------------------- reward helpers
def _helpers():
    """The server's own payers, used for the preview so the promise is the real figure."""
    import first_clear
    import mission_gil
    from lapis_economy import earned

    def lapis(mission_id):
        if not first_clear.is_due(_CTX["ps"], mission_id):
            return 0
        return sum(earned(row["count"]) for row in first_clear.rewards(mission_id) if row["kind"] == 50)

    def gil(mission_id):
        return mission_gil.amount(mission_id)

    def units(mission_id):
        if not first_clear.is_due(_CTX["ps"], mission_id):
            return []
        return [row for row in first_clear.rewards(mission_id) if row["kind"] == 10]

    return {"lapis": lapis, "gil": gil, "units": units, "items": lambda mission_id: []}


_CTX = {"ps": None}


def _book(mst_dir, ps):
    _CTX["ps"] = ps
    return story.load_story(mst_dir, os.environ.get("TEXT_DIR"), helpers=_helpers())


# ------------------------------------------------------------------------------- plan building
def _plan(book, progress_edit):
    if not isinstance(progress_edit, dict):
        raise QuestlineError("progress: expected an object")
    if "region" in progress_edit:
        region, area = progress_edit.get("region"), str(progress_edit.get("area") or "")
        if book.area(area) is None:
            raise QuestlineError(f"progress: area {area!r} is not a story dungeon area")
        return book.checkpoint_plan(int(region), area)
    if "dungeons" in progress_edit or "quests" in progress_edit:
        return book.side_plan(progress_edit)
    raise QuestlineError("progress: needs a region/area checkpoint or a dungeons/quests selection")


# ------------------------------------------------------------------------------------ the run
def _snapshot(ps):
    data = ps.data
    return {
        "lapis": int((data.get("currencies") or {}).get("lapis_free") or 0),
        "gil": int((data.get("currencies") or {}).get("gil") or 0),
        "roster": len(data.get("roster") or []),
        "cleared": len((data.get("progress") or {}).get("cleared_missions") or []),
        "beasts": len(data.get("beasts") or []),
        "quests": len(((data.get("exvius_map") or {}).get("quests")) or {}),
    }


def _restore(ps, saved):
    if saved["attempt"] is None:
        ps.data.get("exvius_repeat", {}).pop("attempt", None)
    else:
        ps.data.setdefault("exvius_repeat", {})["attempt"] = saved["attempt"]
    if saved["fc_last"] is None:
        (ps.data.get("first_clear") or {}).pop("last", None)
    else:
        ps.data.setdefault("first_clear", {})["last"] = saved["fc_last"]
    if saved["mission_gil"] is None:
        ps.data.pop("exvius_mission_gil", None)
    else:
        ps.data["exvius_mission_gil"] = saved["mission_gil"]


def _state_saved(ps, keys=("attempt", "fc_last", "mission_gil")):
    return {
        "attempt": copy.deepcopy((ps.data.get("exvius_repeat") or {}).get("attempt")),
        "fc_last": copy.deepcopy((ps.data.get("first_clear") or {}).get("last")),
        "mission_gil": copy.deepcopy(ps.data.get("exvius_mission_gil")),
    }


def _trial(book, mission_id):
    row = book.missions.get(str(mission_id)) or {}
    switch = str(row.get("juA0Z4m7") or "").strip()
    return bool(switch) and book.switch_type.get(switch) == story.TRIAL_SWITCH_TYPE


def _clear_missions(ps, book, missions, warnings):
    """The pipeline's own order, with an increasing synthetic attempt. Returns the net paid figures."""
    from ffbe_battle import grant_cleared_mission
    import first_clear
    import mission_gil
    import story_units
    from ffbe_config import RESULT_TAG

    cleared = set((ps.data.get("progress") or {}).get("cleared_missions") or [])
    sequence = int((ps.data.get("exvius_repeat") or {}).get("attempt", {}).get("sequence", 0))
    ran = []
    for mission_id in missions:
        if mission_id in cleared:
            continue
        if _trial(book, mission_id):
            warnings.append(f"{mission_id}: a trial mission was in the plan; skipped")
            continue
        sequence += 1
        ps.data.setdefault("exvius_repeat", {})["attempt"] = {
            "mid": mission_id, "sequence": sequence, "status": "completed", "cleared": True}
        spec = {RESULT_TAG: [{}]}
        with contextlib.redirect_stdout(io.StringIO()):
            spec = first_clear.pay(spec, mission_id, RESULT_PLAINTEXT)
            spec = mission_gil.pay(spec, mission_id, RESULT_PLAINTEXT)
            spec = grant_cleared_mission(spec, mission_id, RESULT_PLAINTEXT)
            story_units.grant(spec, None)
        ran.append(mission_id)
    return ran


def _write_gates(ps, book, plan):
    """Every passed area's gate — towns included, so the raw sub-table is the lookup, not `area()`."""
    written = []
    gates_of = {}
    for row in book.sub:
        gates_of.setdefault(str(row.get("6a9TDRcj")), row)
    for area_id in plan.get("gates") or []:
        row = gates_of.get(str(area_id))
        if row is None:
            continue
        for switch in story._ids(row.get("dIPkNn61")):
            pos = player_state.switch_position(switch)
            if pos and pos[1] not in ps.switches.get(pos[0], set()):
                ps.grant_switch(*pos)
                written.append(switch)
    for switch in story.ORPHAN_GATE_SWITCHES:
        pos = player_state.switch_position(switch)
        if pos and pos[1] not in ps.switches.get(pos[0], set()):
            ps.grant_switch(*pos)
            written.append(switch)
    return written


def _delivery_items(book, quest_id, ps):
    """Grant what a delivery step needs, the way the server grants it (kind 21 needs `_grant_items`)."""
    granted = []
    for row in book.quest_steps(quest_id):
        if str(row.get("89EvGKHx")) != "1" or str(row.get("siLGXq94")) != "1":
            continue
        for token in str(row.get("25oxcKwN") or "").split(","):
            parts = token.split(":")
            if len(parts) < 3:
                continue
            kind, item, count = int(parts[0]), parts[1], int(parts[2])
            if kind == 21:
                ps._grant_items([{"kind": 21, "id": item, "count": count}])
            else:
                ps.grant_item(kind, item, count)
            granted.append(f"{kind}:{item}")
    return granted


def _run_quests(ps, book, quest_ids, warnings):
    from exvius_actions_expedition import mission_update

    done, refused = [], []
    for quest_id in quest_ids:
        quest = book.quests.get(str(quest_id))
        if quest is None:
            raise QuestlineError(f"quest {quest_id!r} is not in F_QUEST_MST")
        kind = "town" if str(quest.get("Lxj5u3WB")) == "2" else "mission"
        place = str(quest.get("6uIYE15X") or "")
        node = ({"JcxS6A8N": [{"Gh92V3Tx": place}]} if kind == "town"
                else {"jQsE54Iz": [{"qo3PECw6": place}]})
        _delivery_items(book, quest_id, ps)
        before = json.dumps((ps.data.get("exvius_map") or {}).get("quests") or {}, sort_keys=True)
        spec = {}
        with contextlib.redirect_stdout(io.StringIO()):
            for step in book.quest_steps(quest_id):
                step_id = str(step.get("7Svg2kdT"))
                spec = mission_update(spec, {"1sGiqV9T": [{"i9han5Sz": f"{quest_id}:{step_id}"}], **node})
                spec = mission_update(spec, {"1sGiqV9T": [{"9YPZuB2i": f"{quest_id}:{step_id}"}], **node})
        after = json.dumps((ps.data.get("exvius_map") or {}).get("quests") or {}, sort_keys=True)
        if before == after:
            refused.append(str(quest_id))
            warnings.append(f"quest {quest_id}: refused (no step recorded)")
        else:
            done.append(str(quest_id))
    return done, refused


def _grant_esper(ps, beast_id):
    """Only ever add an esper the save does not own (`grant_beast` rewrites an owned one)."""
    if getattr(ps, "owns_beast", None) and ps.owns_beast(beast_id):
        return False
    if any(str(row.get("esper_id")) == str(beast_id) for row in ps.data.get("beasts") or []):
        return False
    ps.grant_beast(beast_id) if hasattr(ps, "grant_beast") else None
    return True


# ------------------------------------------------------------------------------------ the API
def preview(ps, mst_dir, progress_edit):
    """Run the whole thing on a copy of the save: the cheapest promise that cannot lie."""
    book = _book(mst_dir, ps)
    plan = _plan(book, progress_edit)
    before = _snapshot(ps)
    import ffbe_state

    scratch = copy.deepcopy(ps)
    saved = _state_saved(scratch)
    warnings = []
    real = ffbe_state.State.player
    ffbe_state.State.player = scratch        # the payers write through the global player
    try:
        _clear_missions(scratch, book, plan["missions"], warnings)
        _write_gates(scratch, book, plan)
        done, refused = _run_quests(scratch, book, plan.get("quests") or [], warnings)
        _restore(scratch, saved)
    finally:
        ffbe_state.State.player = real
    after = _snapshot(scratch)
    expected = {
        "currencies": {"lapis_free": after["lapis"] - before["lapis"],
                       "gil": after["gil"] - before["gil"]},
        "roster_added": after["roster"] - before["roster"],
        "extra": {"cleared": after["cleared"] - before["cleared"],
                  "beasts": after["beasts"] - before["beasts"],
                  "quests": len(done), "quests_refused": refused},
    }
    return {"plan": plan, "expected": expected, "warnings": warnings,
            "cleared": after["cleared"] - before["cleared"],
            "espers": after["beasts"] - before["beasts"]}


def apply(ps, mst_dir, progress_edit):
    """Run for real, then prove it delivered at least what the preview promised."""
    started = time.time()
    book = _book(mst_dir, ps)
    plan = _plan(book, progress_edit)
    declared = progress_edit.get("expected") or {}
    before = _snapshot(ps)
    saved = _state_saved(ps)
    warnings = []
    ran = _clear_missions(ps, book, plan["missions"], warnings)
    gates = _write_gates(ps, book, plan)
    done, refused = _run_quests(ps, book, plan.get("quests") or [], warnings)
    for esper in plan.get("espers") or []:
        _grant_esper(ps, esper["esper_id"])
    _restore(ps, saved)
    after = _snapshot(ps)
    paid_lapis = after["lapis"] - before["lapis"]
    paid_gil = after["gil"] - before["gil"]
    roster_added = after["roster"] - before["roster"]

    if declared:
        want_lapis = int((declared.get("currencies") or {}).get("lapis_free") or 0)
        want_gil = int((declared.get("currencies") or {}).get("gil") or 0)
        want_roster = int(declared.get("roster_added") or 0)
        if paid_lapis < want_lapis or paid_gil < want_gil or roster_added < want_roster:
            raise QuestlineError(
                "the run paid less than the preview promised: lapis %d/%d, gil %d/%d, roster %d/%d"
                % (paid_lapis, want_lapis, paid_gil, want_gil, roster_added, want_roster))
    if refused:
        raise QuestlineError("quests refused: " + ", ".join(refused))
    issues = player_state.state_coherence_issues(ps.data)
    if issues:
        raise QuestlineError("the staged save is incoherent: " + "; ".join(issues[:3]))
    return {"cleared": len(ran), "skipped": len(plan["missions"]) - len(ran),
            "quests": len(done), "espers_granted": after["beasts"] - before["beasts"],
            "gates": len(gates), "paid_lapis": paid_lapis, "paid_gil": paid_gil,
            "roster_added": roster_added, "elapsed": round(time.time() - started, 2),
            "warnings": warnings}
