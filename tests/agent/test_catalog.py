"""Catalog tests against the shipped tables.

Every expected value here was measured on the real data (2026-11-21), not guessed:
17 F_TICKET_MST singles; 10+1 tickets are banner costs; 88 sublimation materials (40 cryst,
18 forticite); wiki list 233/236 — the 3 misses are wiki-only umbrella names
("Lid & Jake Fragment", "Master's Crown (Unit-specific)", "Unit's Prism").

Run inside the server image:  tests/run_in_container.sh tests.agent.test_catalog -v
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, REPO)

import player_state  # noqa: E402
from agent import catalog  # noqa: E402

STATIC = os.path.dirname(player_state.MST_DIR)
SERVER_DIR = os.path.dirname(os.path.abspath(player_state.__file__))
WIKI = os.path.join(REPO, "data", "wiki_materials.txt")


class CatalogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cat = catalog.build(STATIC, SERVER_DIR)

    def ticket(self, item_type, item_id):
        for row in self.cat["tickets"]:
            if row["item_type"] == item_type and row["item_id"] == item_id:
                return row
        return None

    def test_ticket_1209001623_is_spendable_11_pulls(self):
        row = self.ticket(20, 1209001623)
        self.assertIsNotNone(row)
        self.assertTrue(row["spendable"])
        self.assertEqual(row["pulls"], 11)
        self.assertIn("9200019", row["note"])

    def test_908_909_marked_unspendable(self):
        for item_id in (908, 909):
            row = self.ticket(23, item_id)
            self.assertIsNotNone(row, item_id)
            self.assertFalse(row["spendable"])
            self.assertEqual(row["item_type"], 23)

    def test_lightning_chain(self):
        self.assertEqual(self.cat["awaken_chain"]["213000105"],
                         ["213000106", "213000107", "213000117"])

    def test_wiki_materials_match_233_of_236(self):
        names = {row["name"].strip().lower() for row in self.cat["materials"]}
        with open(WIKI, encoding="utf-8") as fh:
            wiki = [line.strip() for line in fh if line.strip()]
        self.assertEqual(len(wiki), 236)  # the list is pinned; do not regenerate it
        missed = [name for name in wiki if name.lower() not in names]
        self.assertGreaterEqual(len(wiki) - len(missed), 233, f"missed: {missed}")
        self.assertEqual(len(missed), 3, missed)

    def test_cryst_ladder_40_items(self):
        cryst = [row for row in self.cat["materials"] if row["family"] == "cryst"]
        self.assertEqual(len(cryst), 40)
        self.assertTrue(all(row["item_id"].startswith("2700") for row in cryst))
        self.assertTrue(all(row["name"].lower().endswith("cryst") for row in cryst))

    def test_forticite_18_items(self):
        forticite = [row for row in self.cat["materials"] if row["family"] == "forticite"]
        self.assertEqual(len(forticite), 18)

    def test_names_english(self):
        units = {row["master_id"]: row for row in self.cat["units"]}
        self.assertEqual(units["213000105"]["name"], "Lightning")
        self.assertEqual(units["213000117"]["name"], "Lightning")
        self.assertEqual(units["213000105"]["rarity"], 5)
        self.assertEqual(units["213000107"]["rarity"], 7)
        self.assertEqual(units["213000117"]["nv_group"], "213000105")

    def test_units_carry_the_games_display_number(self):
        """`No. 2706` is F_UNIT_MST.Z0EN6jSh, not the master id (100044907)."""
        units = {row["master_id"]: row for row in self.cat["units"]}
        self.assertEqual(units["100044907"]["no"], 2706)
        self.assertEqual(units["100044907"]["max_level"], 120)
        self.assertEqual(units["100044927"]["no"], 2707)
        self.assertEqual(units["100044927"]["max_level"], 130)
        self.assertEqual(units["100044927"]["lb_cap"], 50)
        self.assertTrue(units["100044907"]["nv"] and units["100044927"]["nv"])
        self.assertEqual(units["100044907"]["name"], "Azure Knight Lasswell")

    def test_nv_plus_form_is_in_the_chain(self):
        self.assertEqual(self.cat["awaken_chain"]["100044907"], ["100044927"])

    def test_units_exclude_nameless_ids(self):
        """A granted unit with no MST_UNIT_NAME_ row crashes the client's award screen (SW-78)."""
        named = player_state.named_unit_ids()
        self.assertTrue(named)
        for row in self.cat["units"]:
            self.assertIn(row["master_id"], named)
            self.assertTrue(row["name"])

    def test_materials_are_all_real_bag_items(self):
        bag = {str(row.get("tL6G9egd")) for row in catalog._rows(os.path.join(STATIC, "mst_decoded"),
                                                                 "F_ITEM_MST")}
        self.assertTrue(self.cat["materials"])
        for row in self.cat["materials"]:
            self.assertIn(row["item_id"], bag)

    def test_divine_crystal_and_masters_crown(self):
        by_id = {row["item_id"]: row for row in self.cat["materials"]}
        self.assertEqual(by_id["290050500"]["name"], "Divine Crystal")
        self.assertEqual(by_id["235037700"]["name"], "Master's Crown")

    def test_points_match_the_server(self):
        from exvius_actions_shops import POINT_TOKENS, POINT_TYPES
        self.assertEqual(len(self.cat["points"]), len(POINT_TYPES))
        self.assertEqual(self.cat["points"][0], {"token": "XeRVw3s2", "type": "EXP"})
        self.assertEqual([row["token"] for row in self.cat["points"]], list(POINT_TOKENS))

    def test_vision_cards_named(self):
        cards = {row["vc_id"]: row for row in self.cat["vision_cards"]}
        self.assertEqual(len(self.cat["vision_cards"]), 339)
        self.assertEqual(cards["100000201"]["name"], "Knights who Soar the Skies")
        self.assertGreater(cards["100000201"]["max_level"], 1)

    def test_xp_units_are_the_9xxxxx_families(self):
        self.assertTrue(self.cat["xp_units"])
        self.assertTrue(all(row["master_id"].startswith("9") for row in self.cat["xp_units"]))
        names = {row["name"] for row in self.cat["xp_units"]}
        self.assertIn("King Tough Pot", names)


if __name__ == "__main__":
    unittest.main()
