"""The unit builder.

Expected numbers are measured, not derived from the code under test:
- Lightning 213000105 at level 5: `total_exp` 658 (steps 21+96+204+337, the live experiment), stats
  [1011, 44, 40, 35, 34, 34] (player_state.unit_stats, hand-checked against the live save).
- LB caps: 20 at 5★, 25 at 6★, 30 at 7★/NV (`ENwn42DV` and the LB level table agree).
- Sol 100031607 EX+2 pays `10:906000103:1:100:1:0:0:0:1100031607` — a Super-Trust Moogle at 1000.

Run inside the server image:  tests/run_in_container.sh tests.agent.test_units -v
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
AGENT_SCRIPT = os.path.join(REPO, "agent", "agent.py")
FIXTURE = os.path.join(REPO, "tests", "fixtures", "campaign.json")
SCRATCH = os.environ.get("TEST_SCRATCH") or tempfile.mkdtemp(prefix="agent-test-")

sys.path.insert(0, REPO)
import player_state  # noqa: E402
from agent import ops_units  # noqa: E402

LIGHTNING = "213000105"
LIGHTNING_7 = "213000107"
LIGHTNING_NV = "213000117"
SOL = "100031607"
TRUST_EQUIP = 409006300
MOOGLE = "906000103"


def run_cli(*args):
    proc = subprocess.run([sys.executable, AGENT_SCRIPT, *args], capture_output=True, text=True)
    try:
        return json.loads(proc.stdout), proc.returncode
    except ValueError:
        raise AssertionError(f"no JSON.\nstdout={proc.stdout!r}\nstderr={proc.stderr!r}") from None


class UnitBuilderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir=SCRATCH)
        self.save = os.path.join(self.tmp, "campaign.json")
        shutil.copyfile(FIXTURE, self.save)
        self.ps = player_state.PlayerState.load(self.save, slot="campaign")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def roster(self, master_id):
        return [row for row in self.ps.data["roster"] if str(row["master_id"]) == master_id]

    def equipment(self, equip_id):
        for row in self.ps.data["inventory"].get("equipment", []):
            if int(row["item_id"]) == equip_id:
                return int(row["count"])
        return 0

    def test_lightning_level_5_matches_experiment(self):
        unit = ops_units.build_unit(self.ps, {"master_id": LIGHTNING, "level": 5, "lb_level": 1})
        self.assertEqual(unit["level"], 5)
        self.assertEqual(unit["total_exp"], 658)
        self.assertEqual(unit["exp"], 0)

    def test_form_chain(self):
        unit = ops_units.build_unit(self.ps, {"master_id": LIGHTNING_NV, "level": 1})
        self.assertEqual(unit["master_id"], LIGHTNING_NV)
        with self.assertRaises(ValueError):
            ops_units.build_unit(self.ps, {"master_id": "213000999", "level": 1})

    def test_form_change_on_an_owned_instance(self):
        unit = ops_units.build_unit(self.ps, {"master_id": LIGHTNING, "level": 5})
        original = str(unit["instance_id"])
        changed = ops_units.build_unit(self.ps, {"master_id": LIGHTNING_7, "level": 5},
                                       instance_id=original)
        self.assertEqual(changed["master_id"], LIGHTNING_7)
        self.assertEqual(changed["level"], 5)
        # the awakening path renews the instance id and removes the old row
        self.assertNotEqual(str(changed["instance_id"]), original)
        self.assertIsNone(self.ps.roster_row(original))

    def test_super_trust_requires_7_star(self):
        with self.assertRaises(ValueError):
            ops_units.build_unit(self.ps, {"master_id": LIGHTNING, "level": 1, "super_trust": 500})
        unit = ops_units.build_unit(self.ps, {"master_id": LIGHTNING_7, "level": 1, "super_trust": 1000})
        self.assertEqual(unit["affinity_points"][1], "1000")

    def test_trust_cap_enforced(self):
        with self.assertRaises(ValueError):
            ops_units.build_unit(self.ps, {"master_id": LIGHTNING, "level": 1, "trust": 1001})
        unit = ops_units.build_unit(self.ps, {"master_id": LIGHTNING, "level": 1, "trust": 1000})
        self.assertEqual(unit["affinity_points"][0], "1000")

    def test_trust_master_reward_granted_once(self):
        spec = {"master_id": LIGHTNING_7, "level": 1, "trust": 1000, "grant_trust_reward": True}
        unit = ops_units.build_unit(self.ps, spec)
        self.assertEqual(self.equipment(TRUST_EQUIP), 1)
        self.assertIn("0:21:409006300:1", unit.get("affinity_reward_claimed") or [])
        again = ops_units.build_unit(self.ps, spec, instance_id=unit["instance_id"])
        self.assertEqual(self.equipment(TRUST_EQUIP), 1)   # not paid twice
        self.assertEqual(again["affinity_points"][0], "1000")

    def test_grant_trust_reward_needs_the_cap(self):
        with self.assertRaises(ValueError):
            ops_units.build_unit(self.ps, {"master_id": LIGHTNING_7, "level": 1, "trust": 500,
                                           "grant_trust_reward": True})

    def test_ex_rewards_sol_ex2_contains_super_trust_moogle(self):
        ops_units.build_unit(self.ps, {"master_id": SOL, "level": 1, "nv_ex_level": 2,
                                       "grant_ex_rewards": True})
        moogles = self.roster(MOOGLE)
        self.assertEqual(len(moogles), 1)
        self.assertEqual(moogles[0]["affinity_points"][1], "1000")
        self.assertEqual(int(moogles[0]["level"]), 1)

    def test_ex_rewards_cards_have_exp_fields(self):
        from exvius_actions_shops import _card_progress
        before = {str(card["user_vc_id"]) for card in self.ps.data["vision_cards"]}
        ops_units.build_unit(self.ps, {"master_id": SOL, "level": 1, "nv_ex_level": 2,
                                       "grant_ex_rewards": True})
        fresh = [card for card in self.ps.data["vision_cards"]
                 if str(card["user_vc_id"]) not in before]
        self.assertTrue(fresh)                      # EX+1 and EX+2 both pay cards
        for card in fresh:
            self.assertIn("exp", card)              # `_grant` writes both fields...
            self.assertIn("total_exp", card)
            _card_progress(card, player_state.vision_card_mst(card["vc_id"]))

    def test_ex_level_requires_the_nv_row(self):
        with self.assertRaises(ValueError):
            ops_units.build_unit(self.ps, {"master_id": LIGHTNING, "level": 1, "nv_ex_level": 1})
        unit = ops_units.build_unit(self.ps, {"master_id": LIGHTNING_NV, "level": 1, "nv_ex_level": 3})
        self.assertEqual(unit["nv_ex_level"], 3)

    def test_ex_level_above_3_rejected(self):
        with self.assertRaises(ValueError):
            ops_units.build_unit(self.ps, {"master_id": LIGHTNING_NV, "level": 1, "nv_ex_level": 4})

    def test_preview_matches_hand_computed_stats(self):
        preview = ops_units.preview_unit({"master_id": LIGHTNING, "level": 5, "lb_level": 1})
        self.assertEqual(preview["stats"], [1011, 44, 40, 35, 34, 34])
        self.assertEqual(preview["max_level"], 80)
        self.assertEqual(preview["lb_cap"], 20)
        self.assertEqual(preview["trust_cap"], 1000)
        self.assertFalse(preview["super_trust_enabled"])
        self.assertEqual(preview["awaken_chain"], ["213000106", "213000107", "213000117"])

    def test_preview_returns_the_units_pot_and_door_caps(self):
        """Each unit carries its own caps in F_UNIT_MST (descriptor fields 3 and 4 per stat)."""
        preview = ops_units.preview_unit({"master_id": LIGHTNING, "level": 1})
        self.assertEqual(len(preview["stat_caps"]), 6)
        self.assertEqual(preview["stat_caps"][0], [900, 450])      # Lightning 5★ HP: pot 900, door 450
        nv = ops_units.preview_unit({"master_id": LIGHTNING_NV, "level": 1})
        self.assertEqual(nv["max_level"], 120)
        self.assertEqual(nv["lb_cap"], 30)
        rydia = ops_units.preview_unit({"master_id": "204003007", "level": 1})
        self.assertEqual(rydia["max_level"], 120)
        self.assertEqual(rydia["lb_cap"], 40)                      # a real NV row, not Lightning's 30

    def test_preview_reflects_pots(self):
        plain = ops_units.preview_unit({"master_id": LIGHTNING, "level": 5})
        pimped = ops_units.preview_unit({"master_id": LIGHTNING, "level": 5,
                                         "stat_pots": [100, 0, 0, 0, 0, 0],
                                         "stat_doors": [50, 0, 0, 0, 0, 0]})
        # `owned_unit_stats` adds pots only; doors RAISE the pot cap and add no stat of their own.
        self.assertEqual(pimped["stats"][0] - plain["stats"][0], 100)

    def test_lb_level_zero_rejected(self):
        with self.assertRaises(ValueError):
            ops_units.build_unit(self.ps, {"master_id": LIGHTNING, "level": 1, "lb_level": 0})

    def test_lb_level_above_cap_rejected(self):
        with self.assertRaises(ValueError):
            ops_units.build_unit(self.ps, {"master_id": LIGHTNING, "level": 1, "lb_level": 21})
        unit = ops_units.build_unit(self.ps, {"master_id": LIGHTNING_7, "level": 1, "lb_level": 30})
        self.assertEqual(unit["lb_level"], 30)

    def test_level_above_cap_rejected(self):
        with self.assertRaises(ValueError):
            ops_units.build_unit(self.ps, {"master_id": LIGHTNING, "level": 81})

    def test_pot_caps_enforced(self):
        with self.assertRaises(ValueError):
            ops_units.build_unit(self.ps, {"master_id": LIGHTNING, "level": 1,
                                           "stat_pots": [901, 0, 0, 0, 0, 0]})
        with self.assertRaises(ValueError):
            ops_units.build_unit(self.ps, {"master_id": LIGHTNING, "level": 1,
                                           "stat_doors": [451, 0, 0, 0, 0, 0]})

    def test_built_unit_passes_coherence(self):
        ops_units.build_unit(self.ps, {"master_id": LIGHTNING, "level": 5, "lb_level": 2,
                                       "trust": 500, "stat_pots": [10, 0, 0, 0, 0, 0]})
        self.assertEqual(player_state.state_coherence_issues(self.ps.data), [])

    def test_cli_units_roundtrip(self):
        edits = os.path.join(self.tmp, "edits.json")
        out = os.path.join(self.tmp, "out.json")
        with open(edits, "w", encoding="utf-8") as fh:
            json.dump({"units": [{"instance_id": None,
                                  "spec": {"master_id": LIGHTNING, "level": 5, "lb_level": 2}}]}, fh)
        payload, code = run_cli("apply", self.save, edits, "--out", out, "--workdir", self.tmp)
        self.assertEqual(code, 0, payload)
        with open(out, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(len(data["roster"]), 16)      # 15 + the built unit
        built = [row for row in data["roster"] if str(row["master_id"]) == LIGHTNING][-1]
        self.assertEqual((built["level"], built["total_exp"], built["lb_level"]), (5, 658, 2))
        # apply wrote the deltas it actually made; validate re-checks the written file against them
        check, check_code = run_cli("validate", out, "--baseline", FIXTURE,
                                    "--expected", os.path.join(self.tmp, "expected.json"))
        self.assertEqual(check_code, 0, check)

    def test_expected_roster_added_counts_reward_units(self):
        edits = {"units": [
            {"instance_id": None, "spec": {"master_id": SOL, "level": 1, "nv_ex_level": 2,
                                           "grant_ex_rewards": True}},
            {"instance_id": "1795117892112", "spec": {"master_id": SOL, "level": 2}},
        ]}
        self.assertEqual(ops_units.expected_roster_added(edits), 2)   # the built Sol + the moogle


if __name__ == "__main__":
    unittest.main()
