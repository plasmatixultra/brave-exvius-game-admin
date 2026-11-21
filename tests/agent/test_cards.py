"""Vision Card grants and level edits.

Card 100000201 (the fixture's card): pattern 110, cap 10, steps {2:100, 3:100, 4:100, 5:100,
6:200, 7:200, 8:200, 9:200, 10:300} — cumulative 0 / 100 / 200 / 400 / 1500 at levels 1 / 2 / 3 /
5 / 10. Hand-summed from `F_VISION_CARD_EXP_PATTERN_MST`, not from the function under test.

Run inside the server image:  tests/run_in_container.sh tests.agent.test_cards -v
"""

import json
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
from agent import ops_cards  # noqa: E402

CARD = "100000201"
OWNED = "1795117892112"


class VisionCardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir=SCRATCH)
        self.save = os.path.join(self.tmp, "campaign.json")
        shutil.copyfile(FIXTURE, self.save)
        self.ps = player_state.PlayerState.load(self.save, slot="campaign")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cards(self):
        return self.ps.data["vision_cards"]

    def test_level_1_matches_threshold_0(self):
        ids = ops_cards.apply_cards(self.ps, [{"vc_id": CARD, "level": 1}])
        self.assertEqual(len(ids), 1)
        card = self.cards()[-1]
        self.assertEqual(card["level"], 1)
        self.assertEqual(card["exp"], 0)
        self.assertEqual(card["total_exp"], 0)

    def test_level_n_total_is_step_sum(self):
        ops_cards.apply_cards(self.ps, [{"vc_id": CARD, "level": 5}, {"vc_id": CARD, "level": 10}])
        five, ten = self.cards()[-2], self.cards()[-1]
        self.assertEqual(five["total_exp"], 100 + 100 + 100 + 100)          # levels 2..5
        self.assertEqual(ten["total_exp"], 400 + 200 + 200 + 200 + 200 + 300)  # + levels 6..10
        self.assertEqual(ops_cards.card_total_exp(CARD, 3), 200)

    def test_over_cap_rejected(self):
        with self.assertRaises(ValueError):
            ops_cards.apply_cards(self.ps, [{"vc_id": CARD, "level": 11}])
        with self.assertRaises(ValueError):
            ops_cards.apply_cards(self.ps, [{"vc_id": CARD, "level": 0}])
        self.assertEqual(len(self.cards()), 1)  # only the fixture's card

    def test_unknown_card_rejected(self):
        with self.assertRaises(ValueError):
            ops_cards.apply_cards(self.ps, [{"vc_id": "999999999", "level": 1}])

    def test_two_grants_unique_ids(self):
        ids = ops_cards.apply_cards(self.ps, [{"vc_id": CARD, "level": 2}, {"vc_id": CARD, "level": 2}])
        self.assertEqual(len(set(ids)), 2)
        self.assertEqual(len({row["user_vc_id"] for row in self.cards()}), len(self.cards()))

    def test_set_card_level_existing_passes_progress_check(self):
        ops_cards.set_card_level(self.ps, OWNED, 5)
        card = next(row for row in self.cards() if row["user_vc_id"] == OWNED)
        self.assertEqual((card["level"], card["exp"], card["total_exp"]), (5, 0, 400))
        master = player_state.vision_card_mst(CARD)
        ops_cards.check_card(card, master)   # raises when the server would reject it

    def test_set_card_level_unknown_id_rejected(self):
        with self.assertRaises(ValueError):
            ops_cards.set_card_level(self.ps, "123", 2)

    def test_set_card_level_over_cap_rejected(self):
        with self.assertRaises(ValueError):
            ops_cards.set_card_level(self.ps, OWNED, 11)

    def test_progress_check_accepts_every_produced_row(self):
        from exvius_actions_shops import _card_progress
        ops_cards.apply_cards(self.ps, [{"vc_id": CARD, "level": lvl} for lvl in (1, 2, 5, 9, 10)])
        for card in self.cards():
            master = player_state.vision_card_mst(card["vc_id"])
            self.assertIsNotNone(master, card["vc_id"])
            _card_progress(card, master)   # Refused (ValueError) fails the test

    def test_cli_grants_and_levels(self):
        edits = os.path.join(self.tmp, "edits.json")
        out = os.path.join(self.tmp, "out.json")
        with open(edits, "w", encoding="utf-8") as fh:
            json.dump({"cards": [{"vc_id": CARD, "level": 4}],
                       "card_levels": [{"user_vc_id": OWNED, "level": 2}]}, fh)
        import subprocess
        proc = subprocess.run([sys.executable, os.path.join(REPO, "agent", "agent.py"),
                               "apply", self.save, edits, "--out", out],
                              capture_output=True, text=True)
        payload = json.loads(proc.stdout)
        self.assertEqual(proc.returncode, 0, payload)
        with open(out, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(len(data["vision_cards"]), 2)   # the fixture's + the granted one
        owned = next(row for row in data["vision_cards"] if row["user_vc_id"] == OWNED)
        self.assertEqual((owned["level"], owned["total_exp"]), (2, 100))


if __name__ == "__main__":
    unittest.main()
