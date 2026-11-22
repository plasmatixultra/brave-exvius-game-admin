"""Settings resolution: where the save, the backups and the data live."""

import json
import os
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import sys
sys.path.insert(0, ROOT)

import config
from tests.fakedocker import FakeDocker

FIXTURES = os.path.join(ROOT, "tests", "fixtures")


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        return json.load(handle)


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
        custom = os.path.join(os.path.realpath(tempfile.mkdtemp()), "server")
        os.makedirs(custom)
        settings = config.from_inspect(
            fixture("docker_inspect_standard.json")[0],
            {"FFBE_SERVER_DIR": custom, "ADMIN_PORT": "9000"})
        self.assertEqual(settings.server_dir, custom)
        self.assertEqual(settings.save, os.path.join(custom, "state", "campaign.json"))
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
        self.assertNotIn("error:", str(caught.exception))

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
        served = os.path.join(os.path.realpath(tempfile.mkdtemp()), "server")
        data = os.path.join(os.path.dirname(served), "data")
        os.makedirs(served)
        os.makedirs(data)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write('[admin]\nserver_dir = "%s"\ndata_dir = "%s"\nport = 8800\n' % (served, data))
        settings = config.load({"ADMIN_CONFIG": path, "FFBE_CONTAINER": "no-such-container"})
        self.assertEqual(settings.server_dir, served)
        self.assertEqual(settings.port, 8800)

    def test_a_typo_in_the_toml_is_refused(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "admin.toml")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write('[admin]\nserver_dri = "/srv/game/server"\n')
        with self.assertRaises(config.SettingsError) as caught:
            config.load({"ADMIN_CONFIG": path, "FFBE_CONTAINER": "nosuch"})
        self.assertIn("server_dri", str(caught.exception))

    def test_a_misspelled_section_is_refused(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "admin.toml")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write('[Admin]\nserver_dir = "/srv/game/server"\n')
        with self.assertRaises(config.SettingsError):
            config.load({"ADMIN_CONFIG": path, "FFBE_CONTAINER": "nosuch"})

    def test_a_relative_directory_is_refused(self):
        with self.assertRaises(config.SettingsError) as caught:
            config.from_inspect(fixture("docker_inspect_standard.json")[0],
                                {"FFBE_SERVER_DIR": "exvius/server"})
        self.assertIn("absolute", str(caught.exception))

    def test_a_missing_directory_is_refused(self):
        with self.assertRaises(config.SettingsError) as caught:
            config.from_inspect(fixture("docker_inspect_standard.json")[0],
                                {"FFBE_SERVER_DIR": "/definitely/not/here"})
        self.assertIn("/definitely/not/here", str(caught.exception))

    def test_env_beats_toml(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "admin.toml")
        served = os.path.join(os.path.realpath(tempfile.mkdtemp()), "server")
        other = os.path.join(os.path.dirname(served), "other")
        os.makedirs(served)
        os.makedirs(other)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write('[admin]\nserver_dir = "%s"\ndata_dir = "%s"\n' % (served, served))
        settings = config.load({"ADMIN_CONFIG": path, "FFBE_SERVER_DIR": other})
        self.assertEqual(settings.server_dir, other)


if __name__ == "__main__":
    unittest.main()
