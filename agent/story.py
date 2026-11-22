"""The story and side-content catalog: a pure read of the shipped master tables.

Nothing here writes anything, touches a save, or needs a live container — it turns
`F_STORY_SUB_MST`, `F_MISSION_MST`, `F_QUEST_MST`, `F_TOWN_MST`, `F_DUNGEON_MST`, `F_BEAST_MST` and the
text tables into regions, areas, quests, esper dungeons and plans. The agent's `apply` replays a plan;
this module only decides what the plan is.

    book = load_story(MST_DIR, TEXT_DIR)
    book.regions()            -> 42 regions, arcs by id
    book.areas(22)            -> dungeon areas in (region, area id) order
    book.checkpoint_plan(22, "2210") -> missions, exclusions, gates, rewards
"""

import json
import os
from collections import defaultdict

ARCS = ((1, 9, "Season 1"), (10, 19, "Paladia"), (20, 29, "The Beyond"), (30, 10 ** 9, "Rivonia"))
DUNGEON_KIND = "3"          # F_STORY_SUB_MST.Lxj5u3WB: 3 = dungeon, 2 = town
TRIAL_SWITCH_TYPE = "821"   # F_SWITCH_MST: awakening trials never clear
ORPHAN_GATES = ("205", "1401")   # prerequisites no area opens; the run writes them by hand
ORPHAN_GATE_SWITCHES = ("10034003", "10271004")   # their switches, written by hand


def _rows(mst_dir, table):
    path = os.path.join(mst_dir, table + ".ndjson")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _texts(text_dir, table, prefix):
    """{id: name} from a text table (`MST_TOWN_NAME_1101` -> {"1101": "Town of Mitra"})."""
    out = {}
    if not text_dir:
        return out
    path = os.path.join(text_dir, table + ".ndjson")
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            key = str(row.get("key") or "")
            if not key.startswith(prefix):
                continue
            values = row.get("v") or []
            out[key[len(prefix):]] = values[0] if values else ""
    return out


def _ids(value):
    return [token.strip() for token in str(value or "").split(",") if token.strip() not in ("", "0")]


def arc_for(region_id):
    for low, high, name in ARCS:
        if low <= int(region_id) <= high:
            return name
    return "Other"


class Story:
    def __init__(self, missions, sub, beasts, quests, towns, dungeons, switches, texts, helpers=None,
                 explorations=()):
        self.helpers = helpers or {}
        self.texts = texts
        self.missions = {str(row["qo3PECw6"]): row for row in missions}
        self.sub = sub
        self.beasts = {str(row.get("Iwfx42Wo")): row for row in beasts}   # F_BEAST_MST beast id
        self.quests = {str(row["Z34jU7ue"]): row for row in quests}
        self.towns = {str(row.get("Gh92V3Tx")): row for row in towns}
        self.dungeons = {str(row.get("U9iHsau3")): row for row in dungeons}
        self.switch_type = {str(key): str(value) for key, value in switches.items()}
        self.explorations = set(map(str, explorations))

        self.dungeon_areas = [row for row in sub if str(row.get("Lxj5u3WB")) == DUNGEON_KIND]
        self.area_rows = {str(row["6a9TDRcj"]): row for row in self.dungeon_areas}
        self.key_areas = defaultdict(set)
        for row in self.dungeon_areas:
            self.key_areas[str(row["6uIYE15X"])].add(str(row["6a9TDRcj"]))
        self.assignment = self._assign()

    # ---------------------------------------------------------------- the mission -> area join
    def _assign(self):
        """Which area owns each mission.

        A mission joins by equality (`U9iHsau3` == `6uIYE15X`). Where several areas share one key, the
        mission belongs to the area its required switch points at: the first area in the group whose
        prerequisite or gate IS that switch, else the area of the mission that grants it (following the
        chain), else it clears at the group's first visit and the later areas are labelled.
        """
        granted_by = defaultdict(list)
        for mission_id, row in self.missions.items():
            for switch in _ids(row.get("dIPkNn61")):
                granted_by[switch].append(mission_id)
        order = self._area_order()
        assignment = {}
        for mission_id, row in self.missions.items():
            key = str(row.get("U9iHsau3") or "")
            areas = self.key_areas.get(key)
            if not areas:
                continue
            if len(areas) == 1:
                assignment[mission_id] = next(iter(areas))
                continue
            ordered = [area for area in order if area in areas]
            switch = str(row.get("juA0Z4m7") or "").strip()
            found = None
            if switch and switch != "0":
                for area in ordered:
                    gate = self.area_rows[area]
                    if switch == str(gate.get("juA0Z4m7") or "").strip():
                        found = area            # the mission sits where its own prerequisite is
                        break
                    if switch == str(gate.get("dIPkNn61") or "").strip():
                        # the area is already cleared, so the mission belongs to the NEXT one
                        index = ordered.index(area)
                        found = ordered[index + 1] if index + 1 < len(ordered) else area
                        break
                if found is None:
                    seen, current = {switch}, switch
                    for _ in range(20):
                        givers = [g for g in granted_by.get(current, []) if self.area_of_key(g)]
                        if not givers:
                            break
                        giver = min(givers, key=int)
                        area = self.area_of_key(giver)
                        if area in areas:
                            # The giver's area has been cleared, so the mission belongs to the group's
                            # NEXT area (measured: 22 of 2,544 cases otherwise land one area early).
                            index = ordered.index(area)
                            found = ordered[index + 1] if index + 1 < len(ordered) else area
                            break
                        nxt = str(self.missions[giver].get("juA0Z4m7") or "").strip()
                        if not nxt or nxt in seen:
                            break
                        seen.add(nxt)
                        current = nxt
            assignment[mission_id] = found or ordered[0]
        return assignment

    def _area_order(self):
        return [str(row["6a9TDRcj"]) for row in sorted(
            self.dungeon_areas, key=lambda r: (int(r["0J2VIT8w"]), int(r["6a9TDRcj"])))]

    def area_of_key(self, mission_id):
        row = self.missions.get(str(mission_id))
        return str(row.get("U9iHsau3") or "") if row else ""

    def missions_of(self, area_id):
        return sorted((m for m, area in self.assignment.items() if area == str(area_id)), key=int)

    # ------------------------------------------------------------------------------ structure
    def regions(self):
        names = self.texts.get("story", {})
        out = []
        for region_id in sorted({int(row["0J2VIT8w"]) for row in self.sub}):
            areas = self.areas(region_id)
            name = names.get(str(region_id)) or ""
            if not name:
                name = "Region %d" % region_id
            out.append({"region_id": region_id, "name": name, "arc": arc_for(region_id),
                        "dungeon_count": len(areas)})
        return out

    def areas(self, region_id):
        region_id = int(region_id)
        out = []
        owners = defaultdict(list)
        for area in self._area_order():
            owners[str(area)].append(area)
        for row in sorted(self.dungeon_areas, key=lambda r: (int(r["0J2VIT8w"]), int(r["6a9TDRcj"]))):
            row_region = int(row["0J2VIT8w"])
            area_id = str(row["6a9TDRcj"])
            missions = self.missions_of(area_id)
            shares = None
            if not missions:
                key = str(row["6uIYE15X"])
                group = sorted(self.key_areas.get(key, ()), key=int)
                owner = next((a for a in group if a != area_id
                              and str((self.area_rows.get(a) or {}).get("0J2VIT8w")) == str(row_region)
                              and self.missions_of(a)), None)
                shares = owner or next((a for a in group if a != area_id and self.missions_of(a)), None)
            if row_region != region_id:
                continue
            out.append({"area_id": area_id, "region_id": row_region,
                        "name": self.texts.get("area", {}).get(area_id) or str(row.get("G4L0YIB2") or ""),
                        "missions": missions, "shares_with": shares})
        return out

    def area(self, area_id):
        row = self.area_rows.get(str(area_id))
        if row is None:
            return None
        area_id = str(area_id)
        return {"area_id": area_id, "region_id": int(row["0J2VIT8w"]),
                "name": self.texts.get("area", {}).get(area_id) or str(row.get("G4L0YIB2") or ""),
                "missions": self.missions_of(area_id),
                "shares_with": None}

    def area_for_mission(self, mission_id):
        area_id = self.assignment.get(str(mission_id))
        return self.area(area_id) if area_id else None

    # -------------------------------------------------------------------------- quests, espers
    def _beat_of(self, mission_id):
        """The esper a mission's clear grants, from its own open-switch list (`820<EE>100`)."""
        row = self.missions.get(str(mission_id)) or {}
        for switch in _ids(row.get("dIPkNn61")):
            if len(switch) == 8 and switch.startswith("820") and switch.endswith("100"):
                beast = int(switch[3:5])
                if str(beast) in self.beasts:      # the table is keyed by string ids
                    return beast
        return None

    def _world_of_place(self, node_kind, place_id):
        if node_kind == "town":
            return str((self.towns.get(str(place_id)) or {}).get("9Pb24aSy") or "")
        return str((self.missions.get(str(place_id)) or {}).get("9Pb24aSy") or "")

    def worlds(self):
        town_names = self.texts.get("town", {})
        beast_names = self.texts.get("beast", {})
        quests_by_world = defaultdict(list)
        for quest_id, row in sorted(self.quests.items(), key=lambda kv: int(kv[0])):
            kind = "town" if str(row.get("Lxj5u3WB")) == "2" else "mission"
            place_id = str(row.get("6uIYE15X") or "")
            world = self._world_of_place(kind, place_id)
            delivery = any(str(step.get("89EvGKHx")) == "1" and str(step.get("siLGXq94")) == "1"
                           for step in self.quest_steps(str(quest_id)))
            place = town_names.get(place_id, place_id) if kind == "town" else \
                str((self.missions.get(place_id) or {}).get("G4L0YIB2") or place_id)
            quests_by_world[world].append({"quest_id": str(quest_id),
                                           "name": self.texts.get("quest", {}).get(str(quest_id), ""),
                                           "place": place, "node_kind": kind,
                                           "delivery": delivery,
                                           "reward": str(row.get("dX6cor8j") or "")})
        dungeons_by_world = defaultdict(list)
        for mission_id, beast in ((m, self._beat_of(m)) for m in self.missions):
            if beast is None:
                continue
            dungeon_key = self.area_of_key(mission_id)
            row = self.dungeons.get(dungeon_key) or {}
            gate = (_ids(row.get("juA0Z4m7")) or [""])[0]
            dungeons_by_world[str(self.missions[mission_id].get("9Pb24aSy") or "")].append({
                "dungeon_key": dungeon_key, "world_id": int(self.missions[mission_id].get("9Pb24aSy") or 0),
                "name": self.texts.get("dungeon", {}).get(dungeon_key)
                        or str(row.get("G4L0YIB2") or dungeon_key),
                "esper_id": beast, "esper_name": beast_names.get(str(beast), ""),
                "beat_mission": str(mission_id),
                "missions": sorted([m for m in self.missions if self.area_of_key(m) == dungeon_key],
                                   key=int),
                "grantable": beast != 15,          # Bahamut has no rarity-1 beat
                "gate_quest": self._gate_quest(gate),
            })
        worlds = []
        for world_id in sorted({int(w) for w in list(quests_by_world) + list(dungeons_by_world) if w}):
            key = str(world_id)
            worlds.append({"world_id": world_id, "name": "World %d" % world_id,
                           "quests": quests_by_world.get(key, []),
                           "dungeons": sorted(dungeons_by_world.get(key, []),
                                              key=lambda d: int(d["dungeon_key"]))})
        return worlds

    def _gate_quest(self, switch):
        if not switch:
            return None
        for quest_id, row in self.quests.items():
            if switch in _ids(row.get("dIPkNn61")):
                return str(quest_id)
        return None

    def quest_steps(self, quest_id):
        if not hasattr(self, "_steps"):
            self._steps = defaultdict(list)
            for row in self.quest_steps_rows:
                self._steps[str(row.get("Z34jU7ue"))].append(row)
        return self._steps.get(str(quest_id), [])

    def side_content(self, world_id):
        for world in self.worlds():
            if world["world_id"] == int(world_id):
                return {"quests": world["quests"], "dungeons": world["dungeons"]}
        return {"quests": [], "dungeons": []}

    # --------------------------------------------------------------------------------- plans
    def _checkpoint_areas(self, region_id, area_id):
        key = (int(region_id), int(area_id))
        passed = []
        for row in self.sub:
            row_key = (int(row["0J2VIT8w"]), int(row["6a9TDRcj"]))
            if row_key <= key:
                passed.append(row)
        passed.sort(key=lambda r: (int(r["0J2VIT8w"]), int(r["6a9TDRcj"])))
        return passed

    def _exclusion(self, mission_id):
        if mission_id in self.explorations:
            return "exploration"
        row = self.missions.get(mission_id) or {}
        switch = str(row.get("juA0Z4m7") or "").strip()
        if switch and self.switch_type.get(switch) == TRIAL_SWITCH_TYPE:
            return "trial"
        return None

    def checkpoint_plan(self, region_id, area_id):
        passed = self._checkpoint_areas(region_id, area_id)
        gates = [str(row["6a9TDRcj"]) for row in passed]
        missions, excluded = [], []
        for row in passed:
            if str(row.get("Lxj5u3WB")) != DUNGEON_KIND:
                continue
            for mission_id in self.missions_of(str(row["6a9TDRcj"])):
                reason = self._exclusion(mission_id)
                if reason:
                    excluded.append({"id": mission_id, "reason": reason})
                else:
                    missions.append(mission_id)
        rewards = {"lapis": 0, "gil": 0, "items": []}
        units = []
        espers = []
        for mission_id in missions:
            rewards["lapis"] += self._helper("lapis", mission_id)
            rewards["gil"] += self._helper("gil", mission_id)
            rewards["items"] += list(self._helper("items", mission_id) or [])
            units += list(self._helper("units", mission_id) or [])
            beast = self._beat_of(mission_id)
            if beast is not None:
                espers.append({"mission_id": mission_id, "esper_id": beast})
        return {"region_id": int(region_id), "area_id": str(area_id), "missions": missions,
                "excluded": excluded, "gates": gates + list(ORPHAN_GATES), "rewards": rewards,
                "units": units, "espers": espers,
                "side_effects": {"claim": "run rewrites the quest audit ring and scores a daily quest"}}

    def _helper(self, name, mission_id):
        helper = self.helpers.get(name)
        return helper(mission_id) if helper else 0

    def side_plan(self, selection):
        """A side-content selection: dungeon keys plus quest ids. Same shape as a checkpoint plan."""
        dungeons = [str(key) for key in selection.get("dungeons") or []]
        quests = [str(qid) for qid in selection.get("quests") or []]
        missions, excluded, gates, espers = [], [], [], []
        for dungeon_key in dungeons:
            row = self.dungeons.get(dungeon_key) or {}
            gate = (_ids(row.get("juA0Z4m7")) or [""])[0]
            if gate and self._gate_quest(gate) in (None, ""):
                gates.append(dungeon_key)
            beat = None
            for mission_id in sorted([m for m in self.missions if self.area_of_key(m) == dungeon_key],
                                     key=int):
                if self._beat_of(mission_id):
                    beat = mission_id
                reason = self._exclusion(mission_id)
                if reason:
                    excluded.append({"id": mission_id, "reason": reason})
                elif mission_id == beat and str(dungeon_key) in (selection.get("no_grant") or []):
                    excluded.append({"id": mission_id, "reason": "esper toggle off"})
                else:
                    missions.append(mission_id)
            if beat and str(dungeon_key) not in (selection.get("no_grant") or []):
                espers.append({"mission_id": beat, "esper_id": self._beat_of(beat)})
        rewards = {"lapis": 0, "gil": 0, "items": []}
        for mission_id in missions:
            rewards["lapis"] += self._helper("lapis", mission_id)
            rewards["gil"] += self._helper("gil", mission_id)
            rewards["items"] += list(self._helper("items", mission_id) or [])
        return {"quests": quests, "dungeons": dungeons, "missions": missions, "excluded": excluded,
                "gates": gates, "rewards": rewards, "units": [], "espers": espers,
                "side_effects": {"claim": "quest completion rewrites the quest audit ring"}}


_CACHE = {}


def load_story(mst_dir, text_dir=None, helpers=None):
    """The parsed catalog, cached per table directory.

    Parsing the full tables takes seconds; the app asks for regions, areas, a plan and each world's
    side content on ONE view load, so an uncached parse made that view unusable.
    """
    key = (os.path.abspath(mst_dir), os.path.abspath(text_dir) if text_dir else None)
    book = _CACHE.get(key)
    if book is not None:
        book.helpers = helpers or {}
        return book
    missions = _rows(mst_dir, "F_MISSION_MST")
    sub = _rows(mst_dir, "F_STORY_SUB_MST")
    beasts = _rows(mst_dir, "F_BEAST_MST")
    quests = _rows(mst_dir, "F_QUEST_MST")
    steps = _rows(mst_dir, "F_QUEST_SUB_MST")
    towns = _rows(mst_dir, "F_TOWN_MST")
    dungeons = _rows(mst_dir, "F_DUNGEON_MST")
    switches = {str(row.get("8vAYn3IX")): row.get("5opcUT9t") for row in _rows(mst_dir, "F_SWITCH_MST")}
    texts = {"story": _texts(text_dir, "F_TEXT_STORY_NAME", "MST_STORY_NAME_"),
             "area": _texts(text_dir, "F_TEXT_STORY_SUB", "MST_STORYSUB_NAME_"),
             "quest": _texts(text_dir, "F_TEXT_QUEST", "MST_QUEST_NAME_"),
             "dungeon": _texts(text_dir, "F_TEXT_DUNGEON_NAME", "MST_DUNGEONS_NAME_"),
             "town": _texts(text_dir, "F_TEXT_TOWN_NAME", "MST_TOWN_NAME_"),
             "beast": _texts(text_dir, "F_TEXT_BEAST_NAME", "MST_BEAST_NAME_")}
    try:
        from ffbe_player import _exploration_missions
        explorations = set(map(str, _exploration_missions()))
    except Exception:                      # noqa: BLE001 - outside the image the set is simply empty
        explorations = set()
    book = Story(missions, sub, beasts, quests, towns, dungeons, switches, texts, helpers, explorations)
    book.quest_steps_rows = steps
    _CACHE[key] = book
    return book
