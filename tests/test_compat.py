"""The agent must refuse an engine it cannot run, before the container stops."""

import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import compat

STUB = {
    "player_state": '''
class PlayerState: pass
_load_ndjson = lambda *a, **k: {}
unit_mst = lambda *a, **k: {}
vision_card_mst = lambda *a, **k: {}
max_level = lambda *a, **k: 0
limit_burst = lambda *a, **k: {}
owned_unit_stats = lambda *a, **k: []
switch_position = lambda *a, **k: 0
state_coherence_issues = lambda *a, **k: []
''',
    "ffbe_state": '''
class State: pass
def load_state(*a, **k): return None
def save_state(*a, **k): return None
''',
    "ffbe_battle": '',
    "first_clear": '''
rewards = lambda *a, **k: []
is_due = lambda *a, **k: False
pay = lambda *a, **k: None
last = lambda *a, **k: None
''',
    "mission_gil": '''
amount = lambda *a, **k: 0
pay = lambda *a, **k: None
''',
    "story_units": '''
grant = lambda *a, **k: None
''',
}

COLUMNS = {
    "F_STORY_SUB_MST": {"6uIYE15X": "1", "6a9TDRcj": "1", "0J2VIT8w": "1",
                        "Lxj5u3WB": "3", "juA0Z4m7": "0", "dIPkNn61": "1"},
    "F_DUNGEON_MST": {"U9iHsau3": "1", "juA0Z4m7": "1"},
    "F_BEAST_MST": {"Iwfx42Wo": "1", "Z0EN6jSh": "1"},
}


def make_engine(tmp):
    os.makedirs(tmp, exist_ok=True)
    for name, body in STUB.items():
        with open(os.path.join(tmp, name + ".py"), "w", encoding="utf-8") as handle:
            handle.write(body)
    return tmp


def make_data(tmp):
    mst = os.path.join(tmp, "mst_decoded")
    os.makedirs(mst, exist_ok=True)
    for name, row in COLUMNS.items():
        with open(os.path.join(mst, name + ".ndjson"), "w", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")
    return tmp


class CompatTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="compat-")
        self.engine = make_engine(os.path.join(self.tmp, "engine"))
        self.data = make_data(os.path.join(self.tmp, "data"))

    def test_a_matching_engine_passes(self):
        result = compat.check(self.engine, self.data)
        self.assertTrue(result["ok"], result["missing"])
        self.assertEqual(result["missing"], [])

    def test_a_missing_module_is_named(self):
        os.remove(os.path.join(self.engine, "first_clear.py"))
        result = compat.check(self.engine, self.data)
        self.assertFalse(result["ok"])
        self.assertIn("first_clear", " ".join(result["missing"]))

    def test_a_missing_symbol_is_named_with_its_module(self):
        with open(os.path.join(self.engine, "player_state.py"), "a", encoding="utf-8") as handle:
            handle.write("\ndel switch_position\n")
        result = compat.check(self.engine, self.data)
        self.assertFalse(result["ok"])
        self.assertIn("player_state.switch_position", result["missing"])

    def test_a_missing_column_is_named_with_its_table(self):
        row = dict(COLUMNS["F_STORY_SUB_MST"])
        row.pop("juA0Z4m7")
        with open(os.path.join(self.data, "mst_decoded", "F_STORY_SUB_MST.ndjson"), "w",
                  encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")
        result = compat.check(self.engine, self.data)
        self.assertFalse(result["ok"])
        self.assertIn("F_STORY_SUB_MST.juA0Z4m7", result["missing"])

    def test_the_games_data_root_is_accepted(self):
        """Settings.data_dir is the root holding static/, not static/mst_decoded."""
        root = os.path.join(self.tmp, "data-root")
        os.makedirs(os.path.join(root, "static"))
        os.rename(os.path.join(self.data, "mst_decoded"),
                  os.path.join(root, "static", "mst_decoded"))
        result = compat.check(self.engine, root)
        self.assertTrue(result["ok"], result["missing"])

    def test_a_missing_table_is_named(self):
        os.remove(os.path.join(self.data, "mst_decoded", "F_BEAST_MST.ndjson"))
        result = compat.check(self.engine, self.data)
        self.assertFalse(result["ok"])
        self.assertIn("F_BEAST_MST", " ".join(result["missing"]))

    def test_an_empty_table_is_not_a_failure(self):
        with open(os.path.join(self.data, "mst_decoded", "F_BEAST_MST.ndjson"), "w") as handle:
            handle.write("")
        result = compat.check(self.engine, self.data)
        self.assertTrue(result["ok"], result["missing"])


if __name__ == "__main__":
    unittest.main()
