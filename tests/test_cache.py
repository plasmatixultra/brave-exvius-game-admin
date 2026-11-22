"""The read cache must survive concurrent pulls (app.py is a ThreadingHTTPServer)."""

import json
import os
import shutil
import tempfile
import threading
import time
import unittest

import localdata


class RefreshRaceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cache-race-")
        self._real = (localdata.CACHE, localdata.SAVE, localdata.STAMP,
                      localdata.remote.get_bytes, localdata.sync_data)
        localdata.CACHE = self.tmp
        localdata.SAVE = os.path.join(self.tmp, "save.json")
        localdata.STAMP = os.path.join(self.tmp, ".stamp.json")
        localdata.sync_data = lambda *a, **k: False
        self.payload = json.dumps({"identity": {"handle_name": "Rain"}, "roster": []}).encode()
        self.calls = []

    def tearDown(self):
        (localdata.CACHE, localdata.SAVE, localdata.STAMP,
         localdata.remote.get_bytes, localdata.sync_data) = self._real
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_concurrent_pulls_never_leave_a_broken_save(self):
        """Two requests finding a stale save must not interleave writes into one file."""
        barrier = threading.Barrier(8)

        def slow_pull(path, timeout=30):
            time.sleep(0.2)                   # a real pull is a network round trip
            self.calls.append(path)
            return self.payload

        localdata.remote.get_bytes = slow_pull
        errors = []

        def worker():
            try:
                barrier.wait(timeout=5)       # every request arrives at once
                localdata.refresh_save()
            except Exception as exc:          # noqa: BLE001 - the test records it
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(errors, [])
        with open(localdata.SAVE, "rb") as fh:
            self.assertEqual(json.loads(fh.read()), {"identity": {"handle_name": "Rain"}, "roster": []})
        self.assertEqual(len(self.calls), 1, "a stale save must be pulled once, not once per thread")


class AgentPayloadTest(unittest.TestCase):
    """Two previews can be in flight at once (the side pane debounces, the cart clicks)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cache-agent-")
        self._real = (localdata.CACHE, localdata.SAVE, localdata.env,
                      localdata.sync_data, localdata.refresh_save)
        localdata.CACHE = self.tmp
        localdata.SAVE = os.path.join(self.tmp, "save.json")
        localdata.env = lambda: {}
        localdata.sync_data = lambda *a, **k: False
        localdata.refresh_save = lambda *a, **k: False

    def tearDown(self):
        (localdata.CACHE, localdata.SAVE, localdata.env,
         localdata.sync_data, localdata.refresh_save) = self._real
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_each_call_writes_its_own_edits_file(self):
        seen = []

        class Proc:
            stdout = '{"ok": true, "result": {}}'

        def fake_run(argv, **kwargs):
            path = argv[4]   # python, agent.py, op, SAVE, edits
            seen.append((path, json.load(open(path, encoding="utf-8"))))
            return Proc()

        original = localdata.subprocess.run
        localdata.subprocess.run = fake_run
        try:
            localdata.agent("preview", {"progress": {"quests": ["1"]}})
            localdata.agent("preview", {"progress": {"quests": ["2"]}})
        finally:
            localdata.subprocess.run = original

        self.assertEqual(len(seen), 2)
        self.assertNotEqual(seen[0][0], seen[1][0], "one file per call, not a shared edits.json")
        self.assertEqual(seen[0][1]["progress"]["quests"], ["1"])
        self.assertEqual(seen[1][1]["progress"]["quests"], ["2"])
        self.assertFalse(os.path.exists(seen[0][0]), "the temp payload is cleaned up")
