"""pipeline.apply: staging, the single run, and the exit-code mapping. No ssh.

Run locally:  python3 -m unittest tests.test_pipeline -v
"""

import json
import os
import sys
import unittest
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)

import pipeline  # noqa: E402
import remote  # noqa: E402


class FakeRemote:
    def __init__(self):
        self.calls = []
        self.stdout = "stopping ffbe\nbackup /home/x/exvius/server/state/backups/campaign-1.json\napply complete\n"
        self.stderr = ""
        self.returncode = 0
        self.raise_error = None
        self.edits = None

    def run(self, cmd, **_kwargs):
        self.calls.append(("run", cmd))
        if self.raise_error is not None and "apply.sh" in cmd:
            raise self.raise_error
        return SimpleNamespace(stdout=self.stdout, stderr=self.stderr, returncode=self.returncode)

    def put(self, local, remote_path):
        self.calls.append(("put", local, remote_path))
        if local.endswith("edits.json"):
            with open(local, encoding="utf-8") as fh:
                self.edits = json.load(fh)

    def put_tree(self, local, remote_path):
        self.calls.append(("tree", local, remote_path))

    def kinds(self):
        return [call[0] for call in self.calls]


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeRemote()
        self._real_remote = pipeline.remote
        pipeline.remote = self.fake

    def tearDown(self):
        pipeline.remote = self._real_remote

    def test_preflight_refuses_when_edits_empty(self):
        result = pipeline.apply({})
        self.assertFalse(result["ok"])
        self.assertIn("no edits", result["error"])
        self.assertEqual(self.fake.calls, [])          # nothing uploaded, nothing touched

    def test_preflight_refuses_unknown_group_only(self):
        result = pipeline.apply({"nonsense": 1})
        self.assertFalse(result["ok"])
        self.assertEqual(self.fake.calls, [])

    def test_pipeline_puts_agent_and_edits_then_runs_script_once(self):
        result = pipeline.apply({"currencies": {"gil": 1000}})
        self.assertTrue(result["ok"], result)
        script_runs = [call for call in self.fake.calls
                       if call[0] == "run" and "apply.sh" in call[1]]
        self.assertEqual(len(script_runs), 1)
        self.assertEqual(len([c for c in self.fake.calls if c[0] == "tree"]), 1)
        self.assertEqual(len([c for c in self.fake.calls if c[0] == "put"]), 2)   # apply.sh + edits
        self.assertEqual(self.fake.edits, {"currencies": {"gil": 1000}})

    def test_pipeline_parses_the_backup_path(self):
        result = pipeline.apply({"currencies": {"gil": 1}})
        self.assertEqual(result["backup"],
                         "/home/x/exvius/server/state/backups/campaign-1.json")
        self.assertIsNone(result["error"])
        self.assertIn("apply complete", result["log"])

    def test_pipeline_rejects_a_zero_exit_without_the_completion_marker(self):
        """A trap-rolled-back run can exit 0; the marker is what says the save was replaced."""
        self.fake.stdout = "stopping ffbe\nbackup /b/campaign-1.json\napplying edits\n"
        result = pipeline.apply({"currencies": {"gil": 1}})
        self.assertFalse(result["ok"])
        self.assertIn("did not complete", result["error"])

    def test_pipeline_maps_exit_99(self):
        self.fake.raise_error = remote.RemoteError(99, "bash apply.sh", "apply already running\n")
        result = pipeline.apply({"currencies": {"gil": 1}})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "apply already running")

    def test_pipeline_reports_refusal(self):
        self.fake.raise_error = remote.RemoteError(
            2, "bash apply.sh", "backup /b/campaign-2.json\nERROR: the agent refused\n")
        result = pipeline.apply({"currencies": {"gil": 1}})
        self.assertFalse(result["ok"])
        self.assertEqual(result["backup"], "/b/campaign-2.json")
        self.assertIn("the agent refused", result["error"])

    def test_pipeline_cleans_its_local_workdir(self):
        seen = {}

        def capture(local, remote_path):
            seen["dir"] = local
            FakeRemote.put(self.fake, local, remote_path)

        self.fake.put = capture
        pipeline.apply({"currencies": {"gil": 1}})
        self.assertFalse(os.path.exists(seen["dir"]))


if __name__ == "__main__":
    unittest.main()
