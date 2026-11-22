"""Ability awakening.

Lightning 213000105 (owned in the fixture) has 18 sublimation recipes, including the chain
`202010001` (tier 1) -> `202010002` (tier 2). Measured: `59kRbpKz` is "0" on all 7,122 shipped
recipes and `F_UNIT_EX_BONUS_MST` has no kind-8 rows, so the gate is tested with a synthetic row.

Run inside the server image:  tests/run_in_container.sh tests.agent.test_abilities -v
"""

import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
FIXTURE = os.path.join(REPO, "tests", "fixtures", "campaign.json")
SCRATCH = os.environ.get("TEST_SCRATCH") or tempfile.mkdtemp(prefix="agent-test-")

sys.path.insert(0, REPO)
import player_state  # noqa: E402
from agent import ops_abilities, ops_basic  # noqa: E402

LIGHTNING = "213000105"
INSTANCE = "1795121118926"       # the fixture's Lightning row
TIER1 = "202010001"
TIER2 = "202010002"
SOL_RECIPE = "300000187"         # allowed only for Sol (100031607)


class AbilityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir=SCRATCH)
        self.save = os.path.join(self.tmp, "campaign.json")
        shutil.copyfile(FIXTURE, self.save)
        self.ps = player_state.PlayerState.load(self.save, slot="campaign")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def skills(self, instance=INSTANCE):
        return (self.ps.data.get("exvius_sublimation") or {}).get("skills", {}).get(instance, {})

    def test_list_recipes_reports_the_chain(self):
        rows = ops_abilities.list_recipes(LIGHTNING)
        by_id = {row["recipe_id"]: row for row in rows}
        self.assertIn(TIER1, by_id)
        self.assertIn(TIER2, by_id)
        self.assertEqual(by_id[TIER1]["tier"], 1)
        self.assertEqual(by_id[TIER2]["tier"], 2)
        self.assertEqual(by_id[TIER1]["materials"][0], {"item_id": "270004100", "count": 15})
        self.assertEqual(by_id[TIER1]["gil"], 250000)
        self.assertFalse(any(row["needs_exbonus"] for row in rows))

    def test_recipes_carry_ability_names(self):
        """The picker must show skills, not recipe ids."""
        rows = {row["recipe_id"]: row for row in ops_abilities.list_recipes(LIGHTNING)}
        self.assertEqual(rows["202070001"]["before_name"], "Blitz")
        self.assertEqual(rows["202010001"]["before_name"], "Lightspeed")
        rain = {row["recipe_id"]: row for row in ops_abilities.list_recipes("100000102")}
        self.assertEqual(rain["204550001"]["before_name"], "Leadership")

    def test_nv_form_inherits_its_series_recipes(self):
        """The built NV row (213000117) still sees the series recipes (root 213000105)."""
        nv = {row["recipe_id"] for row in ops_abilities.list_recipes("213000117")}
        self.assertIn("202070001", nv)
        self.assertEqual(len(nv), len(ops_abilities.list_recipes(LIGHTNING)))

    def test_chain_requires_predecessor(self):
        with self.assertRaises(ValueError):
            ops_abilities.apply_ability(self.ps, INSTANCE, TIER2)
        self.assertEqual(self.skills(), {})

    def test_sets_root_to_after(self):
        result = ops_abilities.apply_ability(self.ps, INSTANCE, TIER1)
        self.assertEqual(result["root"], result["before"])
        self.assertEqual(self.skills(), {result["before"]: result["after"]})

    def test_second_tier_advances_the_same_root(self):
        first = ops_abilities.apply_ability(self.ps, INSTANCE, TIER1)
        second = ops_abilities.apply_ability(self.ps, INSTANCE, TIER2)
        self.assertEqual(second["root"], first["root"])
        self.assertEqual(self.skills(), {first["root"]: second["after"]})

    def test_slots_not_written(self):
        ops_abilities.apply_ability(self.ps, INSTANCE, TIER1)
        state = self.ps.data.get("exvius_sublimation") or {}
        self.assertNotIn("slots", state)                  # the paid unlock stays unset
        self.assertNotIn(INSTANCE, state.get("slots") or {})

    def test_disallowed_unit_rejected(self):
        with self.assertRaises(ValueError):
            ops_abilities.apply_ability(self.ps, INSTANCE, SOL_RECIPE)
        self.assertEqual(self.skills(), {})

    def test_already_learned_is_a_noop(self):
        ops_abilities.apply_ability(self.ps, INSTANCE, TIER1)
        again = ops_abilities.apply_ability(self.ps, INSTANCE, TIER1)
        self.assertTrue(again["already"])
        self.assertEqual(len(self.skills()), 1)

    def test_unknown_recipe_rejected(self):
        with self.assertRaises(ValueError):
            ops_abilities.apply_ability(self.ps, INSTANCE, "does-not-exist")

    def test_unknown_instance_rejected(self):
        with self.assertRaises(ValueError):
            ops_abilities.apply_ability(self.ps, "123", TIER1)

    def test_gate_flagged_not_bypassed(self):
        """No shipped recipe is gated (measured), so the gate is exercised with a synthetic row."""
        fake = {"SJ56uhz2": "9999", "59kRbpKz": "1"}
        with self.assertRaises(ValueError):
            ops_abilities.check_exbonus_gate(self.ps, INSTANCE, fake)
        self.assertTrue(ops_abilities.check_exbonus_gate(self.ps, INSTANCE, fake, force=True))
        # an ungated row never raises, forced or not
        ungated = {"SJ56uhz2": TIER1, "59kRbpKz": "0"}
        self.assertFalse(ops_abilities.check_exbonus_gate(self.ps, INSTANCE, ungated))
        self.assertFalse(ops_abilities.check_exbonus_gate(self.ps, INSTANCE, ungated, force=True))

    def test_grant_materials_adds_the_kit(self):
        before = ops_basic.held(self.ps, 20, 270004100)
        result = ops_abilities.apply_ability(self.ps, INSTANCE, TIER1, grant_materials=True)
        self.assertEqual(ops_basic.held(self.ps, 20, 270004100), before + 15)
        self.assertEqual(len(result["granted_materials"]), 4)

    def test_coherence_after_awakening(self):
        ops_abilities.apply_ability(self.ps, INSTANCE, TIER1)
        self.assertEqual(player_state.state_coherence_issues(self.ps.data), [])


if __name__ == "__main__":
    unittest.main()
