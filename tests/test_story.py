"""Project two, Task 4 — the story/side catalog over the FULL shipped tables.

Run in the server image:  tests/run_in_container.sh tests.test_story -v
No mocks, no fixture save: this is a pure read of `/srv/ffbe/data/static/*_decoded`.
"""

import os
import unittest

from agent import story


class StoryCatalogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.book = story.load_story(os.environ["MST_DIR"], os.environ.get("TEXT_DIR"))

    # ---- the join ----------------------------------------------------------------------------
    def test_join_is_equality_not_prefix(self):
        """A side dungeon's beat mission is not a story mission just because its id starts similarly."""
        self.assertIsNone(self.book.area_for_mission("1115005"))

    def test_towns_are_excluded(self):
        """Towns are passed areas but not dungeon areas: area 102 is Lxj5u3WB == 2."""
        self.assertIsNone(self.book.area("102"))

    def test_dungeon_mission_total(self):
        missions = [m for region in self.book.regions() for area in self.book.areas(region["region_id"])
                    for m in area["missions"]]
        self.assertEqual(len(missions), 2544, "2,544 missions join by equality")

    # ---- regions and order -------------------------------------------------------------------
    def test_region41_survives(self):
        ids = [region["region_id"] for region in self.book.regions()]
        self.assertEqual(len(ids), 42)
        self.assertIn(41, ids)
        region41 = next(r for r in self.book.regions() if r["region_id"] == 41)
        self.assertTrue(region41["name"], "region 41 has no F_STORY_MST row: needs a fallback name")
        self.assertEqual(region41["dungeon_count"], len(self.book.areas(41)))

    def test_order_is_region_then_area_id(self):
        last = 0
        for region in self.book.regions():
            for area in self.book.areas(region["region_id"]):
                current = int(area["area_id"])
                self.assertGreaterEqual(current, last, "areas are in (region, area id) order")
                last = current

    def test_arcs_by_region(self):
        arcs = {region["region_id"]: region["arc"] for region in self.book.regions()}
        self.assertEqual(arcs[1], "Season 1")
        self.assertEqual(arcs[12], "Paladia")
        self.assertEqual(arcs[22], "The Beyond")
        self.assertEqual(arcs[35], "Rivonia")

    # ---- shared keys -------------------------------------------------------------------------
    def test_shared_keys_reported(self):
        """Every area left empty by the assignment says which area owns the missions."""
        labelled = []
        for region in self.book.regions():
            for area in self.book.areas(region["region_id"]):
                if area["shares_with"]:
                    labelled.append(area["area_id"])
        self.assertTrue(labelled, "at least one area is a sharer")
        self.assertTrue(all(isinstance(a, str) for a in labelled))

    def test_late_visit_belongs_to_its_area(self):
        """11110204 requires 1010's prerequisite, so it belongs to 1010 and is deferred out of 1008."""
        self.assertEqual(self.book.area_for_mission("11110204")["area_id"], "1010")

    # ---- exclusions --------------------------------------------------------------------------
    def test_trials_and_explorations_excluded(self):
        plan = self.book.checkpoint_plan(22, "2210")
        excluded = {row["id"]: row["reason"] for row in plan["excluded"]}
        # 1110405/6 are Siren's trials and sit in story area 104; 1115006/7 are Ifrit's, in a SIDE
        # dungeon, so they are not part of a story checkpoint at all.
        for mission in ("1110405", "1110406"):
            self.assertEqual(excluded.get(mission), "trial")
        self.assertNotIn("1115006", excluded, "a side dungeon's trials are not in a story plan")
        self.assertEqual(len(plan["excluded"]), 69, "67 explorations + 2 trials")
        self.assertEqual(sum(1 for reason in excluded.values() if reason == "exploration"), 67)

    # ---- quests and worlds -------------------------------------------------------------------
    def test_quest_places_resolve(self):
        worlds = {world["world_id"]: world for world in self.book.worlds()}
        quests = {row["quest_id"]: row for world in worlds.values() for row in world["quests"]}
        self.assertEqual(len(quests), 269)
        mitra = quests["1001001"]
        self.assertEqual(mitra["node_kind"], "town")
        self.assertIn("Mitra", mitra["place"], "1001001 is delivered in the Town of Mitra")

    def test_quest_worlds(self):
        counts = {world["world_id"]: len(world["quests"]) for world in self.book.worlds()}
        filled = {key: value for key, value in counts.items() if value}
        self.assertEqual(sorted(filled.values()), [3, 6, 77, 183])

    def test_esper_beats_have_world(self):
        beats = []
        for world in self.book.worlds():
            beats += [dungeon for dungeon in world["dungeons"] if dungeon.get("esper_id")]
        self.assertEqual(len(beats), 19)
        fenrir = next(d for d in beats if d["esper_id"] == 16)
        self.assertEqual(fenrir["world_id"], 3)
        leviathan = next(d for d in beats if d["esper_id"] == 12)
        self.assertEqual(leviathan["world_id"], 3)

    def test_bahamut_not_grantable(self):
        for world in self.book.worlds():
            for dungeon in world["dungeons"]:
                if dungeon["dungeon_key"] == "19208":
                    self.assertFalse(dungeon["grantable"], "Bahamut (15) has no rarity-1 beat")
                    self.assertIn("1920801", dungeon["missions"])

    def test_gate_quests(self):
        gates = {}
        for world in self.book.worlds():
            for dungeon in world["dungeons"]:
                if dungeon.get("gate_quest"):
                    gates[dungeon["dungeon_key"]] = dungeon["gate_quest"]
        self.assertEqual(gates.get("11251"), "2001001")
        self.assertEqual(gates.get("11355"), "1010007")
        self.assertEqual(gates.get("210102"), "40101002")

    # ---- plans -------------------------------------------------------------------------------
    def test_plan_rewards_are_paid_figures(self):
        """Mission Lapis is the paid figure (earned() applies x5); quest Lapis is raw."""
        book = story.load_story(
            os.environ["MST_DIR"], os.environ.get("TEXT_DIR"),
            helpers={"lapis": lambda mission: 500, "gil": lambda mission: 1000,
                     "items": lambda mission: [], "units": lambda mission: []})
        plan = book.checkpoint_plan(22, "2210")
        self.assertEqual(plan["rewards"]["lapis"], 500 * len(plan["missions"]))
        self.assertEqual(plan["rewards"]["gil"], 1000 * len(plan["missions"]))

    def test_plan_gates_cover_every_passed_area(self):
        plan = self.book.checkpoint_plan(22, "2210")
        self.assertIn("2201", plan["gates"])
        self.assertIn("205", plan["gates"], "the two orphan prerequisites are named by their area")
        self.assertIn("1401", plan["gates"])


if __name__ == "__main__":
    unittest.main()
