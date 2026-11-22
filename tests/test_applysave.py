"""The apply sequence, against a recording docker double.

Nothing here starts a container: the fake docker records argv and answers
`run … apply-files.sh backup` with a backup path, so the test can assert the
order of every step and what happens when one of them fails.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import applysave
import config
import transport
from tests.fakedocker import FakeDocker


def make_settings(tmp, cache):
    server = os.path.join(tmp, "server")
    data = os.path.join(tmp, "data")
    os.makedirs(os.path.join(server, "state", "backups"))
    os.makedirs(data)
    return config.from_inspect([{
        "Name": "/ffbe",
        "Config": {"Image": "ffbe-ios-server:local"},
        "Mounts": [{"Destination": config.CONTAINER_SERVER_ROOT, "Source": server},
                   {"Destination": config.CONTAINER_DATA, "Source": data}]}])


class ApplyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cache = os.path.join(self.tmp, "cache")
        self.settings = make_settings(self.tmp, self.cache)
        self.app = os.path.join(self.tmp, "app")
        os.makedirs(self.app)
        self.saved_env = dict(os.environ)
        os.environ["XDG_CACHE_HOME"] = self.cache

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.saved_env)

    def test_the_sequence_is_stop_backup_apply_validate_write_start(self):
        with FakeDocker() as fake:
            result = applysave.apply_edits(self.settings, {"currencies": {"gil": 1}}, app_dir=self.app)
        self.assertTrue(result["ok"], result)
        calls = [c for c in fake.argv() if c != "inspect ffbe"]
        self.assertEqual(calls[0], "stop ffbe")
        verbs = [c.split("apply-files.sh ")[1].split()[0] for c in calls if "apply-files.sh" in c]
        self.assertEqual(verbs, ["backup", "apply", "validate", "write"])
        self.assertEqual(calls[-2], "start ffbe")
        self.assertTrue(calls[-1].startswith("exec ffbe"))

    def test_the_backup_path_is_reported(self):
        expected = os.path.join(self.settings.backups, "campaign-20261122T201500Z.json")
        os.environ["FAKE_BACKUP"] = expected
        with FakeDocker():
            result = applysave.apply_edits(self.settings, {}, app_dir=self.app)
        self.assertEqual(result["backup"], expected)

    def test_a_refusal_restores_and_restarts(self):
        with FakeDocker():
            os.environ["FAKE_AGENT_FAILS"] = "1"
            result = applysave.apply_edits(self.settings, {"currencies": {"gil": 1}}, app_dir=self.app)
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], 2)
        self.assertIn("restore", " ".join(result["log"]))
        self.assertFalse(os.path.exists(applysave.marker_path()))

    def test_rollback_hands_the_backup_path_to_the_container(self):
        """The workdir is owned by the image's user after the backup; the host must not
        write into it, so the path travels as an argument."""
        expected = os.path.join(self.settings.backups, "campaign-20261122T201500Z.json")
        os.environ["FAKE_BACKUP"] = expected
        os.environ["FAKE_AGENT_FAILS"] = "1"
        with FakeDocker() as fake:
            result = applysave.apply_edits(self.settings, {}, app_dir=self.app)
        self.assertFalse(result["ok"])
        restores = [c for c in fake.argv() if "apply-files.sh restore" in c]
        self.assertEqual(len(restores), 1, fake.argv())
        self.assertIn(expected, restores[0])
        self.assertNotIn("ROLLBACK FAILED", " ".join(result["log"]))

    def test_a_refusal_never_reports_success(self):
        with FakeDocker():
            os.environ["FAKE_AGENT_FAILS"] = "1"
            result = applysave.apply_edits(self.settings, {}, app_dir=self.app)
        self.assertFalse(result["ok"])
        self.assertNotIn("apply complete", "\n".join(result["log"]))

    def test_the_host_never_touches_the_save(self):
        save = os.path.join(self.settings.server_dir, "state", "campaign.json")
        with open(save, "w", encoding="utf-8") as handle:
            handle.write('{"identity": {"handle_name": "Rain"}}')
        with open(save, "rb") as handle:
            before = handle.read()
        with FakeDocker() as fake:
            applysave.apply_edits(self.settings, {}, app_dir=self.app)
        with open(save, "rb") as handle:
            self.assertEqual(handle.read(), before)
        for call in fake.argv():
            self.assertNotIn(save, call)

    def test_a_busy_lock_refuses_without_stopping_anything(self):
        lock = applysave.Lock(applysave.lock_path())
        with lock:
            with FakeDocker() as fake:
                result = applysave.apply_edits(self.settings, {}, app_dir=self.app)
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], applysave.EXIT_BUSY)
        self.assertEqual(fake.argv(), [])

    def test_a_dead_lock_holder_is_replaced(self):
        os.makedirs(os.path.dirname(applysave.lock_path()), exist_ok=True)
        with open(applysave.lock_path(), "w", encoding="utf-8") as handle:
            handle.write("999999999\n")
        with FakeDocker():
            result = applysave.apply_edits(self.settings, {}, app_dir=self.app)
        self.assertTrue(result["ok"], result)

    def test_the_status_poll_gives_up_and_restores(self):
        with FakeDocker():
            os.environ["FAKE_STATUS_BAD"] = "1"
            started = subprocess.time.time
            result = applysave.apply_edits(self.settings, {}, app_dir=self.app, status_timeout=0)
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], applysave.EXIT_STATUS_TIMEOUT)
        self.assertIn("restore", " ".join(result["log"]))

    def test_a_server_that_is_still_booting_is_polled_not_failed(self):
        """The game answers only once it has loaded the save; an early refusal is not a failure."""
        os.environ["FAKE_STATUS_FAILURES"] = "2"
        with FakeDocker():
            result = applysave.apply_edits(self.settings, {}, app_dir=self.app, status_timeout=10)
        self.assertTrue(result["ok"], result)
        self.assertNotIn("restore", " ".join(result["log"]))

    def test_a_rollback_is_logged_once(self):
        os.environ["FAKE_AGENT_FAILS"] = "1"
        with FakeDocker():
            result = applysave.apply_edits(self.settings, {}, app_dir=self.app)
        restores = [line for line in result["log"] if "save restored" in line]
        self.assertEqual(len(restores), 1, result["log"])

    def test_recover_restores_a_dead_apply(self):
        workdir = os.path.join(self.cache, "apply-dead")
        os.makedirs(workdir)
        backup = os.path.join(self.settings.backups, "campaign-20261122T200000Z.json")
        with open(backup, "w", encoding="utf-8") as handle:
            handle.write("{}")
        applysave._write_marker(applysave.marker_path(),
                                {"workdir": workdir, "backup": backup, "pid": 999999999})
        with FakeDocker() as fake:
            self.assertTrue(applysave.recover(self.settings, app_dir=self.app))
        verbs = [c for c in fake.argv() if "apply-files.sh" in c]
        self.assertEqual(len(verbs), 1)
        self.assertIn("restore", verbs[0])
        self.assertEqual(fake.argv()[-1], "start ffbe")
        self.assertFalse(os.path.exists(applysave.marker_path()))

    def test_recover_is_quiet_when_nothing_died(self):
        with FakeDocker() as fake:
            self.assertFalse(applysave.recover(self.settings, app_dir=self.app))
        self.assertEqual(fake.argv(), [])


if __name__ == "__main__":
    unittest.main()
