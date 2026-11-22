"""Local reads must equal the in-container numbers.

`localdata` runs the server's own code on the host interpreter (3.14) against cached tables; the
apply path runs it in the image (3.12). This test pins the two together, so a drift shows up here
rather than in the game.

Skipped when the local cache has not been primed (`python3 localdata.py`) or athena is unreachable.
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)

import localdata  # noqa: E402
import pipeline  # noqa: E402
import remote  # noqa: E402

SPECS = [
    {"master_id": "213000105", "level": 80, "lb_level": 20},
    {"master_id": "213000117", "level": 120, "lb_level": 30, "nv_ex_level": 3},
    {"master_id": "100031607", "level": 120, "lb_level": 40, "nv_ex_level": 2,
     "stat_pots": [100, 20, 30, 40, 50, 60], "stat_doors": [50, 0, 10, 0, 0, 12]},
    {"master_id": "201000104", "level": 60, "lb_level": 15},
]


def container_up():
    try:
        remote.run(f"docker inspect -f '{{{{.State.Running}}}}' {pipeline.config.CONTAINER}")
        return True
    except remote.RemoteError:
        return False


@unittest.skipUnless(localdata.cache_ready(), "run `python3 localdata.py` first")
@unittest.skipUnless(container_up(), "the ffbe container is not running")
class ParityTest(unittest.TestCase):
    def test_preview_matches_the_container(self):
        for spec in SPECS:
            with self.subTest(master_id=spec["master_id"]):
                local = localdata.agent("preview", {"unit_spec": spec})["unit"]
                inside = pipeline.query("preview", {"unit_spec": spec})["unit"]
                self.assertEqual(local["stats"], inside["stats"])
                self.assertEqual(local["max_level"], inside["max_level"])
                self.assertEqual(local["lb_cap"], inside["lb_cap"])
                self.assertEqual(local["trust_cap"], inside["trust_cap"])
                self.assertEqual(local["super_trust_cap"], inside["super_trust_cap"])
                self.assertEqual(local["stat_caps"], inside["stat_caps"])

    def test_summary_matches_the_container(self):
        local = localdata.agent("summary")
        inside = pipeline.query("summary")
        self.assertEqual(local["gil"], inside["gil"])
        self.assertEqual(local["lapis_free"], inside["lapis_free"])
        self.assertEqual(len(local["roster"]), len(inside["roster"]))
        self.assertEqual([u["master_id"] for u in local["roster"]],
                         [u["master_id"] for u in inside["roster"]])


if __name__ == "__main__":
    unittest.main()
