"""Local reads must equal the in-container numbers.

`localdata` runs the server's own code on this machine's Python against the image's exported
engine and the game's own tables; the apply path runs it in the image. This test pins the two
together, so a drift shows up here rather than in the game.

Skipped when the cache has not been primed or the container is not running: it needs the real stack.
"""

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)

import applysave  # noqa: E402
import config  # noqa: E402
import localdata  # noqa: E402
import transport  # noqa: E402

SPECS = [
    {"master_id": "213000105", "level": 80, "lb_level": 20},
    {"master_id": "213000117", "level": 120, "lb_level": 30, "nv_ex_level": 3},
    {"master_id": "100031607", "level": 120, "lb_level": 40, "nv_ex_level": 2,
     "stat_pots": [100, 20, 30, 40, 50, 60], "stat_doors": [50, 0, 10, 0, 0, 12]},
    {"master_id": "201000104", "level": 60, "lb_level": 15},
]


def settings():
    return config.load()


def container_up():
    try:
        return transport.running(settings().container)
    except (transport.TransportError, config.SettingsError):
        return False


def container_agent(op, edits, current):
    """Run the same agent op inside the image, against a copy of the live save."""
    mounts = [(REPO, "/admin", "ro"),
              (current.server_dir, "/srv/ffbe/server-root", "ro")]
    script = ("cp /srv/ffbe/server-root/state/campaign.json /tmp/save.json && "
              "python3 /admin/agent/agent.py %s /tmp/save.json" % op)
    if edits is not None:
        with open("/tmp/parity-edits.json", "w", encoding="utf-8") as handle:
            json.dump(edits, handle)
        mounts.append(("/tmp/parity-edits.json", "/tmp/edits.json", "ro"))
        script = script.replace("/tmp/save.json", "/tmp/save.json /tmp/edits.json")
    done = transport.run_in_image(current.image, mounts, ["bash", "-c", script],
                                  env=applysave.AGENT_ENV)
    payload = json.loads(done.stdout)
    if not payload.get("ok"):
        raise AssertionError(payload.get("error"))
    return payload["result"]


@unittest.skipUnless(localdata.cache_ready(), "run the app once so the cache exists")
@unittest.skipUnless(container_up(), "the ffbe container is not running")
class ParityTest(unittest.TestCase):
    def test_preview_matches_the_container(self):
        current = settings()
        for spec in SPECS:
            with self.subTest(master_id=spec["master_id"]):
                local = localdata.agent("preview", {"unit_spec": spec})["unit"]
                inside = container_agent("preview", {"unit_spec": spec}, current)["unit"]
                self.assertEqual(local["stats"], inside["stats"])
                self.assertEqual(local["max_level"], inside["max_level"])
                self.assertEqual(local["lb_cap"], inside["lb_cap"])
                self.assertEqual(local["trust_cap"], inside["trust_cap"])
                self.assertEqual(local["super_trust_cap"], inside["super_trust_cap"])
                self.assertEqual(local["stat_caps"], inside["stat_caps"])

    def test_summary_matches_the_container(self):
        current = settings()
        local = localdata.agent("summary")
        inside = container_agent("summary", None, current)
        self.assertEqual(local["gil"], inside["gil"])
        self.assertEqual(local["lapis_free"], inside["lapis_free"])
        self.assertEqual([u["master_id"] for u in local["roster"]],
                         [u["master_id"] for u in inside["roster"]])


if __name__ == "__main__":
    unittest.main()
