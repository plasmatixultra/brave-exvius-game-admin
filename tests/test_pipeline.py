"""pipeline.apply: the preflight, the one sequence, and the report. No containers.

Run locally:  python3 -m unittest tests.test_pipeline -v
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)

from unittest import mock  # noqa: E402

import applysave  # noqa: E402
import config  # noqa: E402
import pipeline  # noqa: E402
from tests.fakedocker import FakeDocker  # noqa: E402


class PipelineTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="pipeline-test-")
        self.saved_env = dict(os.environ)
        os.environ["XDG_CACHE_HOME"] = os.path.join(self.tmp, "cache")
        server = os.path.join(self.tmp, "server")
        data = os.path.join(self.tmp, "data")
        os.makedirs(os.path.join(server, "state", "backups"), exist_ok=True)
        os.makedirs(data, exist_ok=True)
        # A healthy engine by default; the drift test overrides both of these.
        self.patchers = [
            mock.patch.object(pipeline.engine, "ensure", return_value=os.path.join(self.tmp, "engine")),
            mock.patch.object(pipeline.compat, "check",
                              return_value={"ok": True, "missing": []}),
        ]
        for patcher in self.patchers:
            patcher.start()
        os.makedirs(os.path.join(self.tmp, "cache", "ffbe-admin"), exist_ok=True)
        self.settings = config.from_inspect([{
            "Name": "/ffbe",
            "Config": {"Image": "ffbe-ios-server:local"},
            "Mounts": [{"Destination": config.CONTAINER_SERVER_ROOT, "Source": server},
                       {"Destination": config.CONTAINER_DATA, "Source": data}]}])

    def tearDown(self):
        for patcher in self.patchers:
            patcher.stop()
        os.environ.clear()
        os.environ.update(self.saved_env)

    def test_preflight_refuses_when_edits_empty(self):
        with FakeDocker() as docker:
            result = pipeline.apply({}, settings=self.settings)
            self.assertEqual(docker.argv(), [])
        self.assertFalse(result["ok"])
        self.assertIn("no edits", result["error"])

    def test_preflight_refuses_unknown_group_only(self):
        with FakeDocker() as docker:
            result = pipeline.apply({"nonsense": 1}, settings=self.settings)
            self.assertEqual(docker.argv(), [])
        self.assertFalse(result["ok"])

    def test_engine_drift_is_refused_before_anything_stops(self):
        with FakeDocker() as docker, \
                mock.patch.object(pipeline.engine, "ensure", return_value="/cache/engine"), \
                mock.patch.object(pipeline.compat, "check",
                                  return_value={"ok": False,
                                                "missing": ["first_clear.rewards"]}):
            result = pipeline.apply({"currencies": {"gil": 1}}, settings=self.settings)
            self.assertEqual(docker.argv(), [], "nothing may stop before the engine is checked")
        self.assertFalse(result["ok"])
        self.assertIn("first_clear.rewards", result["error"])

    def test_apply_runs_the_sequence_and_reports(self):
        with FakeDocker() as docker:
            result = pipeline.apply({"currencies": {"gil": 1000}}, settings=self.settings)
        self.assertTrue(result["ok"], result)
        self.assertIsNone(result["error"])
        self.assertIn("save replaced", "\n".join(result["log"]))
        calls = docker.argv()
        self.assertEqual(calls[0], "stop ffbe")
        self.assertEqual(calls[-2], "start ffbe")

    def test_the_backup_path_is_handed_back(self):
        expected = os.path.join(self.settings.backups, "campaign-20261122T201500Z.json")
        os.environ["FAKE_BACKUP"] = expected
        with FakeDocker():
            result = pipeline.apply({"currencies": {"gil": 1}}, settings=self.settings)
        self.assertEqual(result["backup"], expected)

    def test_a_refusal_is_reported_with_its_cause(self):
        with FakeDocker():
            os.environ["FAKE_AGENT_FAILS"] = "1"
            result = pipeline.apply({"currencies": {"gil": 1}}, settings=self.settings)
        self.assertFalse(result["ok"])
        self.assertIn("refused", result["error"])
        self.assertIn("restore", " ".join(result["log"]))

    def test_a_status_timeout_is_not_success(self):
        with FakeDocker():
            os.environ["FAKE_STATUS_BAD"] = "1"
            result = pipeline.apply({"currencies": {"gil": 1}}, settings=self.settings,
                                    status_timeout=0)
        self.assertFalse(result["ok"])
        self.assertIn("healthy", result["error"])

    def test_a_busy_apply_is_reported_in_words(self):
        lock = applysave.Lock(applysave.lock_path())
        with lock:
            with FakeDocker():
                result = pipeline.apply({"currencies": {"gil": 1}}, settings=self.settings)
        self.assertFalse(result["ok"])
        self.assertIn("already running", result["error"])

    def test_no_workdir_is_left_behind(self):
        with FakeDocker():
            pipeline.apply({"currencies": {"gil": 1}}, settings=self.settings)
        leftovers = [name for name in os.listdir(os.environ["XDG_CACHE_HOME"] + "/ffbe-admin")
                     if name.startswith("apply-")]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
