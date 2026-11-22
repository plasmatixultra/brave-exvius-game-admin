"""Tests for the local docker transport, against a recording docker double."""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import transport
from tests.fakedocker import FakeDocker


class TransportTest(unittest.TestCase):
    def test_stop_calls_docker_stop(self):
        with FakeDocker() as fake:
            transport.stop("ffbe")
            self.assertEqual(fake.argv()[-1], "stop ffbe")

    def test_start_calls_docker_start(self):
        with FakeDocker() as fake:
            transport.start("ffbe")
            self.assertEqual(fake.argv()[-1], "start ffbe")

    def test_inspect_returns_first_element(self):
        with FakeDocker():
            payload = transport.inspect("ffbe")
        self.assertEqual(payload["Name"], "/ffbe")
        self.assertEqual(payload["Config"]["Image"], "ffbe-ios-server:local")

    def test_status_returns_the_line(self):
        with FakeDocker() as fake:
            line = transport.status("ffbe")
            self.assertIn('"roster": 44', line)
            self.assertIn("exec ffbe python3 /app/ffbe.py status", fake.argv()[-1])

    def test_failure_raises_with_stderr(self):
        with FakeDocker():
            with self.assertRaises(transport.TransportError) as caught:
                transport.docker(["stop", "missing"])
        self.assertEqual(caught.exception.returncode, 1)
        self.assertIn("No such container", str(caught.exception))

    def test_run_in_image_mounts_each_path(self):
        with FakeDocker() as fake:
            transport.run_in_image(
                "ffbe-ios-server:local",
                [("/a", "/srv/ffbe/server-root", "rw"), ("/b", "/work", "rw")],
                ["/admin/runner/apply-files.sh", "backup"], entrypoint="bash")
            call = fake.argv()[-1]
        self.assertTrue(call.startswith("run --rm"), call)
        self.assertIn("-v /a:/srv/ffbe/server-root", call)
        self.assertIn("-v /b:/work", call)
        self.assertIn("--entrypoint bash", call)
        self.assertIn("ffbe-ios-server:local /admin/runner/apply-files.sh backup", call)

    def test_missing_docker_is_reported(self):
        saved = os.environ["PATH"]
        os.environ["PATH"] = "/nonexistent"
        try:
            with self.assertRaises(transport.TransportError) as caught:
                transport.docker(["ps"])
        finally:
            os.environ["PATH"] = saved
        self.assertEqual(caught.exception.returncode, 127)


if __name__ == "__main__":
    unittest.main()
