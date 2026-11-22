"""Settings resolution: where the save, the backups and the data live."""

import json
import os
import shutil
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import sys
sys.path.insert(0, ROOT)

import config

FIXTURES = os.path.join(ROOT, "tests", "fixtures")


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        return json.load(handle)


class FakeDocker:
    """Puts tests/fixtures/fake-docker on PATH for the duration of a test."""

    def __init__(self):
        self.dir = tempfile.mkdtemp()
        self.calls = os.path.join(self.dir, "calls")
        shutil.copy(os.path.join(FIXTURES, "fake-docker"), os.path.join(self.dir, "docker"))
        os.chmod(os.path.join(self.dir, "docker"), 0o755)
        self.saved = dict(os.environ)

    def __enter__(self):
        os.environ["PATH"] = self.dir + os.pathsep + self.saved["PATH"]
        os.environ["CALLS_FILE"] = self.calls
        os.environ["FAKE_INSPECT_JSON"] = os.path.join(FIXTURES, "docker_inspect_standard.json")
        return self

    def __exit__(self, *exc):
        os.environ.clear()
        os.environ.update(self.saved)

    def argv(self):
        with open(self.calls, encoding="utf-8") as handle:
            return handle.read().splitlines()


class SettingsTest(unittest.TestCase):
    def test_standard_layout(self):
        settings = config.from_inspect(fixture("docker_inspect_standard.json")[0])
        self.assertEqual(settings.server_dir, "/home/revival/exvius/server")
        self.assertEqual(settings.save, "/home/revival/exvius/server/state/campaign.json")
        self.assertEqual(settings.backups, "/home/revival/exvius/server/state/backups")
        self.assertEqual(settings.data_dir, "/home/revival/exvius/data")
        self.assertEqual(settings.image, "ffbe-ios-server:local")
        self.assertEqual(settings.container, "ffbe")

    def test_custom_ffbe_dir(self):
        payload = fixture("docker_inspect_custom_dir.json")[0]
        self.assertEqual(config.from_inspect(payload).server_dir, "/mnt/user/appdata/ffbe/server")

    def test_container_name_comes_from_inspect(self):
        payload = fixture("docker_inspect_standard.json")[0]
        payload["Name"] = "/ffbe-admin-test"
        self.assertEqual(config.from_inspect(payload).container, "ffbe-admin-test")

    def test_missing_mount_is_refused(self):
        payload = fixture("docker_inspect_standard.json")[0]
        payload["Mounts"] = [m for m in payload["Mounts"] if m["Destination"] != "/srv/ffbe/data"]
        with self.assertRaises(config.SettingsError):
            config.from_inspect(payload)

    def test_env_overrides_win(self):
        settings = config.from_inspect(
            fixture("docker_inspect_standard.json")[0],
            {"FFBE_SERVER_DIR": "/srv/custom/server", "ADMIN_PORT": "9000"})
        self.assertEqual(settings.server_dir, "/srv/custom/server")
        self.assertEqual(settings.save, "/srv/custom/server/state/campaign.json")
        self.assertEqual(settings.port, 9000)

    def test_unknown_override_is_refused(self):
        with self.assertRaises(config.SettingsError):
            config.from_inspect(fixture("docker_inspect_standard.json")[0], {"FFBE_SERVER_DIRR": "/x"})


class DetectTest(unittest.TestCase):
    def test_absent_container_is_refused(self):
        with FakeDocker():
            with self.assertRaises(config.SettingsError) as caught:
                config.detect("nosuch")
        self.assertIn("nosuch", str(caught.exception))

    def test_detect_reads_the_container(self):
        with FakeDocker() as fake:
            found = config.detect("ffbe")
        self.assertEqual(found["server_dir"], "/home/revival/exvius/server")
        self.assertEqual(found["data_dir"], "/home/revival/exvius/data")
        self.assertEqual(found["image"], "ffbe-ios-server:local")
        self.assertEqual(fake.argv()[-1], "inspect ffbe")


class LoadTest(unittest.TestCase):
    def test_load_uses_the_toml_when_detection_is_unavailable(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "admin.toml")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write('[admin]\nserver_dir = "/srv/game/server"\ndata_dir = "/srv/game/data"\nport = 8800\n')
        settings = config.load({"ADMIN_CONFIG": path, "FFBE_CONTAINER": "no-such-container"})
        self.assertEqual(settings.server_dir, "/srv/game/server")
        self.assertEqual(settings.port, 8800)

    def test_env_beats_toml(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "admin.toml")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write('[admin]\nserver_dir = "/srv/game/server"\ndata_dir = "/srv/game/data"\n')
        settings = config.load({"ADMIN_CONFIG": path, "FFBE_SERVER_DIR": "/srv/other/server"})
        self.assertEqual(settings.server_dir, "/srv/other/server")


if __name__ == "__main__":
    unittest.main()
