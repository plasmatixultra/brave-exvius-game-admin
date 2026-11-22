"""The read cache: the save is mirrored safely, the engine comes from the image."""

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config  # noqa: E402
import localdata  # noqa: E402
from tests.fakedocker import FakeDocker  # noqa: E402


def make_settings(tmp):
    server = os.path.join(tmp, "server")
    data = os.path.join(tmp, "data")
    os.makedirs(os.path.join(server, "state"), exist_ok=True)
    for name in ("mst_decoded", "text_decoded"):
        os.makedirs(os.path.join(data, "static", name), exist_ok=True)
    return config.from_inspect([{
        "Name": "/ffbe",
        "Config": {"Image": "ffbe-ios-server:local"},
        "Mounts": [{"Destination": config.CONTAINER_SERVER_ROOT, "Source": server},
                   {"Destination": config.CONTAINER_DATA, "Source": data}]}])


class SaveCacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cache-")
        self.cache = os.path.join(self.tmp, "cache")
        self.settings = make_settings(self.tmp)
        self.save = self.settings.save
        self._saved = (localdata.CACHE, localdata.SAVE, localdata._settings)
        localdata.CACHE = self.cache
        localdata.SAVE = os.path.join(self.cache, "save.json")
        localdata._settings = lambda: self.settings
        self.write_save({"identity": {"handle_name": "Rain"}, "roster": []})

    def tearDown(self):
        (localdata.CACHE, localdata.SAVE, localdata._settings) = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_save(self, payload):
        with open(self.save, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)

    def test_the_first_pull_copies_the_save(self):
        self.assertTrue(localdata.refresh_save())
        with open(localdata.SAVE, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["identity"]["handle_name"], "Rain")

    def test_an_unchanged_save_is_not_copied_again(self):
        localdata.refresh_save()
        calls = []
        real = localdata._read_save_bytes
        localdata._read_save_bytes = lambda *a, **k: (calls.append(1), real(*a, **k))[1]
        try:
            self.assertFalse(localdata.refresh_save())
        finally:
            localdata._read_save_bytes = real
        self.assertEqual(calls, [])

    def test_a_changed_save_is_picked_up(self):
        localdata.refresh_save()
        time.sleep(0.01)
        self.write_save({"identity": {"handle_name": "Lasswell"}, "roster": []})
        self.assertTrue(localdata.refresh_save())
        with open(localdata.SAVE, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["identity"]["handle_name"], "Lasswell")

    def test_concurrent_readers_never_leave_a_broken_save(self):
        """The page is a ThreadingHTTPServer: several requests find the save stale at once."""
        barrier = threading.Barrier(8)
        count = []

        def slow_read(*args, **kwargs):
            time.sleep(0.2)                     # a real read crosses a filesystem
            count.append(1)
            return json.dumps({"identity": {"handle_name": "Rain"}, "roster": []}).encode()

        localdata._read_save_bytes = slow_read
        errors = []

        def worker():
            try:
                barrier.wait(timeout=5)
                localdata.refresh_save()
            except Exception as exc:            # noqa: BLE001 - the test records it
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(errors, [])
        with open(localdata.SAVE, "rb") as handle:
            self.assertEqual(json.loads(handle.read()),
                             {"identity": {"handle_name": "Rain"}, "roster": []})
        self.assertEqual(len(count), 1, "a stale save must be read once, not once per thread")

    def test_an_unreadable_save_is_read_through_a_container(self):
        os.chmod(self.save, 0o000)

        def restore_mode():
            try:
                os.chmod(self.save, 0o644)
            except FileNotFoundError:
                pass

        self.addCleanup(restore_mode)
        with FakeDocker():
            os.environ["FAKE_CAT"] = json.dumps({"identity": {"handle_name": "Container"}})
            localdata.refresh_save(force=True)
        with open(localdata.SAVE, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["identity"]["handle_name"], "Container")


class EnvTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="env-")
        self.cache = os.path.join(self.tmp, "cache")
        self.settings = make_settings(self.tmp)
        self._saved = (localdata.CACHE, localdata._settings)
        localdata.CACHE = self.cache
        localdata._settings = lambda: self.settings

    def tearDown(self):
        (localdata.CACHE, localdata._settings) = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_env_points_at_the_exported_engine_and_the_game_data(self):
        with FakeDocker():
            localdata.sync_data()
        env = localdata.env()
        self.assertEqual(env["PYTHONPATH"], os.path.join(self.cache, "engine"))
        self.assertEqual(env["MST_DIR"], os.path.join(self.settings.data_dir, "static", "mst_decoded"))
        self.assertEqual(env["TEXT_DIR"], os.path.join(self.settings.data_dir, "static", "text_decoded"))

    def test_cache_ready_needs_the_engine_and_the_tables(self):
        self.assertFalse(localdata.cache_ready())
        with FakeDocker():
            localdata.sync_data()
        self.assertTrue(localdata.cache_ready())


if __name__ == "__main__":
    unittest.main()
