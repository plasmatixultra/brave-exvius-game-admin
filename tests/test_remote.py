"""Tests for the ssh/scp transport. Requires passwordless key auth to athena."""

import os
import tempfile
import unittest

import remote


class RemoteTest(unittest.TestCase):
    def test_run_echo(self):
        result = remote.run("echo ok")
        self.assertEqual(result.stdout.strip(), "ok")

    def test_run_failure_raises(self):
        with self.assertRaises(remote.RemoteError) as ctx:
            remote.run("exit 3")
        self.assertEqual(ctx.exception.returncode, 3)

    def test_put_roundtrip(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
            fh.write("ffbe-admin roundtrip\n")
            local = fh.name
        try:
            remote.put(local, "/tmp/ffbe-admin-test")
            result = remote.run("cat /tmp/ffbe-admin-test")
            self.assertEqual(result.stdout, "ffbe-admin roundtrip\n")
        finally:
            os.unlink(local)
            try:
                remote.run("rm -f /tmp/ffbe-admin-test")
            except remote.RemoteError:
                pass


if __name__ == "__main__":
    unittest.main()
