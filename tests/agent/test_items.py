"""XP points, materials and recipe kits.

Fixture: gil 2,980,094 · XeRVw3s2 3,500,000 · roster 15.

Run inside the server image:  tests/run_in_container.sh tests.agent.test_items -v
"""

import hashlib
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
from agent import ops_items  # noqa: E402

FIXTURE_EXP = 3500000


def sha256(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def run_cli(*args):
    proc = subprocess.run([sys.executable, AGENT_SCRIPT, *args], capture_output=True, text=True)
    try:
        return json.loads(proc.stdout), proc.returncode
    except ValueError:
        raise AssertionError(f"no JSON.\nstdout={proc.stdout!r}\nstderr={proc.stderr!r}") from None


class ItemsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir=SCRATCH)
        self.save = os.path.join(self.tmp, "campaign.json")
        shutil.copyfile(FIXTURE, self.save)
        self.ps = player_state.PlayerState.load(self.save, slot="campaign")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def bank(self, token="XeRVw3s2"):
        return int(self.ps.data["exvius_unit_points"]["banks"]["0"].get(token, 0))

    def item(self, item_id, item_type=20):
        for row in self.ps.data["inventory"]["items"]:
            if int(row["item_type"]) == item_type and int(row["item_id"]) == item_id:
                return int(row["count"])
        return None

    def test_points_exp_adds_to_XeRVw3s2(self):
        self.assertEqual(self.bank(), FIXTURE_EXP)
        ops_items.apply_points(self.ps, {"EXP": 100000})
        self.assertEqual(self.bank(), FIXTURE_EXP + 100000)

    def test_points_create_missing_token_as_zero_first(self):
        from exvius_actions_shops import POINT_TYPES, POINT_TOKENS
        kind = POINT_TYPES[3]
        token = POINT_TOKENS[3]
        ops_items.apply_points(self.ps, {kind: 7})
        self.assertEqual(self.bank(token), 7)

    def test_unknown_point_type_rejected(self):
        with self.assertRaises(ValueError):
            ops_items.apply_points(self.ps, {"NOPE": 1})

    def test_negative_points_rejected(self):
        with self.assertRaises(ValueError):
            ops_items.apply_points(self.ps, {"EXP": -1})

    def test_material_new_row(self):
        self.assertIsNone(self.item(290050500))
        ops_items.apply_materials(self.ps, [{"item_id": 290050500, "count": 3}])
        self.assertEqual(self.item(290050500), 3)

    def test_material_existing_row_adds(self):
        self.assertEqual(self.item(290010000), 2)
        ops_items.apply_materials(self.ps, [{"item_id": 290010000, "count": 4}])
        self.assertEqual(self.item(290010000), 6)

    def test_material_over_cap_rejected(self):
        from agent import ops_basic
        cap = ops_basic.stack_cap(20, 290010000)
        self.assertIsNotNone(cap)
        with self.assertRaises(ValueError):
            ops_items.apply_materials(self.ps, [{"item_id": 290010000, "count": cap + 1}])

    def test_material_unknown_rejected(self):
        with self.assertRaises(ValueError):
            ops_items.apply_materials(self.ps, [{"item_id": 999999999, "count": 1}])

    def test_recipe_kit_sublimation_10170001(self):
        kit = ops_items.recipe_kit("sublimation", "10170001")
        self.assertEqual(kit["materials"], [
            {"item_id": "270000100", "count": 15},
            {"item_id": "270000200", "count": 8},
            {"item_id": "270000300", "count": 5},
            {"item_id": "270000400", "count": 1},
        ])
        self.assertEqual(kit["gil"], 250000)

    def test_recipe_kit_ex_sol_1(self):
        kit = ops_items.recipe_kit("ex_class_up", "100031607")
        self.assertEqual(kit["materials"], [
            {"item_id": "250000110", "count": 50},
            {"item_id": "293000100", "count": 1},
        ])
        self.assertEqual(kit["gil"], 250000)
        self.assertEqual(kit["level"], 1)

    def test_recipe_kit_ex_level_2(self):
        kit = ops_items.recipe_kit("ex_class_up", "100031607:2")
        self.assertEqual(kit["level"], 2)
        self.assertEqual(kit["gil"], 500000)

    def test_recipe_kit_unknown_recipe_rejected(self):
        with self.assertRaises(ValueError):
            ops_items.recipe_kit("sublimation", "does-not-exist")

    def test_recipe_kit_unknown_table_rejected(self):
        with self.assertRaises(ValueError):
            ops_items.recipe_kit("nope", "1")

    def test_cli_points_and_materials_roundtrip(self):
        edits = os.path.join(self.tmp, "edits.json")
        out = os.path.join(self.tmp, "out.json")
        with open(edits, "w", encoding="utf-8") as fh:
            json.dump({"points": {"EXP": 1000},
                       "materials": [{"item_id": 290050500, "count": 2}]}, fh)
        before = sha256(self.save)
        payload, code = run_cli("apply", self.save, edits, "--out", out)
        self.assertEqual(code, 0, payload)
        with open(out, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(int(data["exvius_unit_points"]["banks"]["0"]["XeRVw3s2"]), FIXTURE_EXP + 1000)
        self.assertEqual([r for r in data["inventory"]["items"]
                          if int(r["item_id"]) == 290050500][0]["count"], 2)
        self.assertEqual(sha256(self.save), before)
        # points and materials change no currency and no roster row
        check, check_code = run_cli("validate", out, "--baseline", FIXTURE)
        self.assertEqual(check_code, 0, check)


if __name__ == "__main__":
    unittest.main()
