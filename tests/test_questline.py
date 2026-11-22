"""Project two, Task 5 — the run itself, on copies of the fixture, with the real server modules."""

import copy
import json
import os
import shutil
import tempfile
import unittest

import player_state

from agent import questline, story

FIXTURE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "tests", "fixtures", "campaign.json")
MST = os.environ["MST_DIR"]
CHECKPOINT = {"region": 22, "area": "2210"}


class QuestlineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="questline-")
        self.path = os.path.join(self.tmp, "campaign.json")
        shutil.copy(FIXTURE, self.path)
        self.ps = player_state.PlayerState.load(self.path, slot="campaign")
        import ffbe_state
        ffbe_state.State.player = self.ps

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def gate_on(self, switch):
        pos = player_state.switch_position(switch)
        return bool(pos) and pos[1] in self.ps.switches.get(pos[0], set())

    # ---- the promise -------------------------------------------------------------------------
    def test_preview_splits_cleared(self):
        result = questline.preview(self.ps, MST, CHECKPOINT)
        plan = result["plan"]
        cleared = set((self.ps.data.get("progress") or {}).get("cleared_missions") or [])
        self.assertEqual(result["cleared"], sum(1 for m in plan["missions"] if m not in cleared))
        self.assertEqual(len(plan["missions"]), 1451)

    def test_expected_deltas_match_actuals(self):
        preview = questline.preview(self.ps, MST, CHECKPOINT)["expected"]
        report = questline.apply(self.ps, MST, CHECKPOINT)
        self.assertEqual(report["paid_lapis"], preview["currencies"]["lapis_free"])
        self.assertEqual(report["paid_gil"], preview["currencies"]["gil"])
        self.assertEqual(report["roster_added"], preview["roster_added"])

    def test_less_than_preview_fails(self):
        edit = dict(CHECKPOINT)
        edit["expected"] = {"currencies": {"lapis_free": 10 ** 9}, "roster_added": 0}
        with self.assertRaises(questline.QuestlineError):
            questline.apply(self.ps, MST, edit)

    # ---- the run -----------------------------------------------------------------------------
    def test_apply_needs_a_declared_expectation_through_the_cli(self):
        """`agent.py apply` refuses a progress run that declares nothing — the side cart's old shape."""
        from agent import agent as agent_cli
        edits = os.path.join(self.tmp, "edits.json")
        with open(edits, "w", encoding="utf-8") as fh:
            json.dump({"progress": {"checkpoint": {"region": 22, "area": "2210"}}}, fh)
        with self.assertRaises(ValueError) as caught:
            agent_cli.op_apply(self.path, edits, {})
        self.assertIn("expected", str(caught.exception))

    def test_a_checkpoint_before_region_4_leaves_its_orphans_shut(self):
        """205 (Dwarves' Forge) needs a switch no area opens; a checkpoint that never passes it, and
        the side selections, must not write it — nor its opened switch, which unlocks area 211."""
        early = {"region": 3, "area": "308"}      # Kolobos Isle, well before region 4
        questline.apply(self.ps, MST, early)
        self.assertFalse(self.gate_on("10034003"), "205's prerequisite belongs to a region-4 checkpoint")
        self.assertFalse(self.gate_on("10048500"), "and 211 must stay shut")

    def test_orphan_prerequisites_need_a_checkpoint(self):
        questline.apply(self.ps, MST, {"checkpoint": None, "side": {"1": {
            "quests": [], "dungeons": [{"dungeon_key": "11151", "grant_esper": True}]}}})
        self.assertFalse(self.gate_on("10034003"), "a side selection unlocks nothing in region 4")

    def test_a_quest_gated_dungeon_brings_its_quest(self):
        plan = story.load_story(MST).side_plan({"dungeons": ["11251"], "quests": []})
        self.assertIn("2001001", plan["quests"], "Zadehl Southersands is opened by quest 2001001")
        self.assertEqual(plan["gate_quests"], [{"dungeon_key": "11251", "quest_id": "2001001"}])

    def test_the_promise_is_read_from_one_place(self):
        """The cart puts `expected` beside `progress`; the flat test shapes put it inside. One reader,
        so a caller that sets both is not counted twice."""
        from agent import agent as agent_cli
        both = {"expected": {"roster_added": 3},
                "progress": {"expected": {"roster_added": 3}}}
        self.assertEqual(agent_cli.declared_expected(both)["roster_added"], 3)
        self.assertEqual(agent_cli.requested_roster(both, []), 3, "not 6")

    def test_a_finished_quest_is_not_a_refusal(self):
        """The second run of the same selection must not die on 'quests refused'."""
        progress = {"checkpoint": None, "side": {"1": {"quests": ["1001001", "1001002"], "dungeons": []}}}
        first = questline.apply(self.ps, MST, progress)
        self.assertEqual(first["quests"], 2)
        second = questline.apply(self.ps, MST, progress)
        self.assertEqual(second["quests"], 2)
        self.assertTrue(all("already complete" in w for w in second["warnings"]))

    def test_preview_promises_the_beast_it_will_grant(self):
        """The preview skipped the esper step, so its `beasts` figure was always 0 while the run added 1."""
        progress = {"checkpoint": None, "side": {"1": {"quests": [], "dungeons": [
            {"dungeon_key": "11151", "grant_esper": True}]}}}
        promise = questline.preview(self.ps, MST, progress)
        self.assertEqual(promise["expected"]["extra"]["beasts"], 1)
        report = questline.apply(self.ps, MST, progress)
        self.assertEqual(report["espers_granted"], 1)

    def test_apply_works_with_no_global_player(self):
        """The agent process never sets `ffbe_state.State.player`; the payers need it anyway."""
        import ffbe_state
        ffbe_state.State.player = None
        report = questline.apply(self.ps, MST, CHECKPOINT)
        self.assertGreater(report["cleared"], 0)
        self.assertIsNone(ffbe_state.State.player, "the global is restored, not left pointing at the run")

    def test_apply_clears_and_reports(self):
        report = questline.apply(self.ps, MST, CHECKPOINT)
        self.assertGreater(report["cleared"], 1400)
        self.assertEqual(report["paid_lapis"] > 0, True)
        self.assertEqual(report["paid_gil"] > 0, True)

    def test_story_units_join_in_run(self):
        before = len(self.ps.data["roster"])
        questline.apply(self.ps, MST, CHECKPOINT)
        self.assertEqual(len(self.ps.data["roster"]) - before, 7, "6 story units + 1 first-clear unit")

    def test_apply_opens_all_gates_towns_too(self):
        questline.apply(self.ps, MST, CHECKPOINT)
        self.assertTrue(self.gate_on("10004003"), "area 103 (a town)'s gate is written")

    def test_side_dungeon_opens_its_own_unlock(self):
        """A side dungeon's node appears only once `F_DUNGEON_MST.juA0Z4m7` is granted — the key is a
        story key, so the sub-table lookup used by checkpoints found nothing and wrote nothing."""
        report = questline.apply(self.ps, MST, {"checkpoint": None,
                                                "side": {"1": {"quests": [],
                                                               "dungeons": [{"dungeon_key": "11151",
                                                                             "grant_esper": True}]}}})
        self.assertGreaterEqual(report["gates"], 1)
        self.assertTrue(self.gate_on("10014001"), "Inferno Hollow's unlock switch")

    def test_non_area_prereqs_written(self):
        questline.apply(self.ps, MST, CHECKPOINT)
        self.assertTrue(self.gate_on("10034003"), "area 205's orphan prerequisite")
        self.assertTrue(self.gate_on("10271004"), "area 1401's orphan prerequisite")

    def test_trials_not_cleared(self):
        questline.apply(self.ps, MST, CHECKPOINT)
        cleared = set(self.ps.data["progress"]["cleared_missions"])
        for trial in ("1110405", "1110406"):
            self.assertNotIn(trial, cleared)

    def test_apply_is_idempotent(self):
        questline.apply(self.ps, MST, CHECKPOINT)
        again = questline.apply(self.ps, MST, CHECKPOINT)
        self.assertEqual(again["cleared"], 0)

    def test_attempt_and_ledgers_restored(self):
        saved = copy.deepcopy((self.ps.data.get("exvius_repeat") or {}).get("attempt"))
        last = copy.deepcopy((self.ps.data.get("first_clear") or {}).get("last"))
        mg = copy.deepcopy(self.ps.data.get("exvius_mission_gil"))
        questline.apply(self.ps, MST, CHECKPOINT)
        self.assertEqual((self.ps.data.get("exvius_repeat") or {}).get("attempt"), saved)
        self.assertEqual((self.ps.data.get("first_clear") or {}).get("last"), last)
        self.assertEqual(self.ps.data.get("exvius_mission_gil"), mg)

    def test_ledger_invariant(self):
        questline.apply(self.ps, MST, CHECKPOINT)
        import first_clear
        paid = set(self.ps.data["first_clear"].get("paid") or [])
        cleared = set(self.ps.data["progress"]["cleared_missions"])
        violations = [m for m in cleared if first_clear.raw(m) and m not in paid]
        self.assertEqual(violations, [])

    def test_coherence_after_run(self):
        questline.apply(self.ps, MST, CHECKPOINT)
        self.assertEqual(player_state.state_coherence_issues(self.ps.data), [])

    # ---- side content ------------------------------------------------------------------------
    def test_esper_toggle_on(self):
        report = questline.apply(self.ps, MST, {"dungeons": ["11151"]})
        self.assertGreaterEqual(report["espers_granted"], 1)
        self.assertTrue(any(str(b["esper_id"]) == "2" for b in self.ps.data["beasts"]))

    def test_esper_toggle_off(self):
        before = copy.deepcopy(self.ps.data.get("beasts"))
        questline.apply(self.ps, MST, {"dungeons": ["11151"], "no_grant": ["11151"]})
        self.assertEqual(self.ps.data.get("beasts"), before)


if __name__ == "__main__":
    unittest.main()
