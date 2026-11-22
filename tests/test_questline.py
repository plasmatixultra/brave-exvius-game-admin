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
