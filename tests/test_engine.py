"""The engine source, exported from the image once per image id."""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config
import engine
from tests.fakedocker import FakeDocker


def make_settings(tmp, image="ffbe-ios-server:local"):
    server = os.path.join(tmp, "server")
    data = os.path.join(tmp, "data")
    os.makedirs(os.path.join(server, "state"), exist_ok=True)
    os.makedirs(data, exist_ok=True)
    return config.from_inspect([{
        "Name": "/ffbe",
        "Config": {"Image": image},
        "Mounts": [{"Destination": config.CONTAINER_SERVER_ROOT, "Source": server},
                   {"Destination": config.CONTAINER_DATA, "Source": data}]}])


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="engine-")
        self.cache = os.path.join(self.tmp, "cache")
        self.settings = make_settings(self.tmp)

    def test_export_lands_in_the_cache(self):
        with FakeDocker():
            path = engine.ensure(self.settings, self.cache)
        self.assertTrue(os.path.exists(os.path.join(path, "player_state.py")))
        self.assertEqual(path, os.path.join(self.cache, "engine"))

    def test_a_second_call_does_not_re_export(self):
        with FakeDocker() as fake:
            first = engine.ensure(self.settings, self.cache)
            stamp = os.path.getmtime(os.path.join(first, "player_state.py"))
            engine.ensure(self.settings, self.cache)
            exports = [c for c in fake.argv() if "cp -a /app/server/." in c]
        self.assertEqual(len(exports), 1)
        self.assertEqual(os.path.getmtime(os.path.join(first, "player_state.py")), stamp)

    def test_a_new_image_re_exports(self):
        with FakeDocker():
            engine.ensure(self.settings, self.cache)
        with FakeDocker(inspect_json="docker_inspect_next_image.json"):
            path = engine.ensure(self.settings, self.cache)
        with open(os.path.join(path, ".image-id"), encoding="utf-8") as handle:
            self.assertEqual(handle.read().strip(), "sha256:next")

    def test_an_image_without_the_server_is_named(self):
        with FakeDocker():
            os.environ["FAKE_NO_SERVER"] = "1"
            with self.assertRaises(engine.EngineError) as caught:
                engine.ensure(self.settings, self.cache)
        self.assertIn("ffbe-ios-server:local", str(caught.exception))
        self.assertFalse(os.path.exists(os.path.join(self.cache, "engine", "player_state.py")))

    def test_a_failed_export_leaves_no_half_tree(self):
        with FakeDocker():
            os.environ["FAKE_NO_SERVER"] = "1"
            with self.assertRaises(engine.EngineError):
                engine.ensure(self.settings, self.cache)
        self.assertEqual([n for n in os.listdir(self.cache) if n.startswith("engine-")], [])


if __name__ == "__main__":
    unittest.main()
