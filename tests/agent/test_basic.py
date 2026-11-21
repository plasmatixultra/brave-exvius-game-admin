"""Agent core: currencies and tickets, plus the refusal and round-trip guarantees.

Fixture pinned by the plan: `tests/fixtures/campaign.json` (baseline-20261120-011457).
Its numbers are asserted here verbatim and used by every later agent test:
    gil 2,980,094   lapis_free 2,208   roster 15   XeRVw3s2 3,500,000
    inventory: 23/180 = 9, 20/290010000 = 2, ...

Run inside the server image:  tests/run_in_container.sh
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
AGENT_SCRIPT = os.path.join(REPO, "agent", "agent.py")
FIXTURE = os.path.join(REPO, "tests", "fixtures", "campaign.json")
SCRATCH = os.environ.get("TEST_SCRATCH") or tempfile.mkdtemp(prefix="agent-test-")

sys.path.insert(0, REPO)
from agent import ops_basic  # noqa: E402  (only importable with the repo root on the path)

FIXTURE_GIL = 2980094
FIXTURE_LAPIS = 2208


def sha256(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def run_cli(*args):
    """Run the agent CLI; return (payload, returncode)."""
    proc = subprocess.run([sys.executable, AGENT_SCRIPT, *args],
                          capture_output=True, text=True)
    try:
        payload = json.loads(proc.stdout)
    except ValueError:
        raise AssertionError(f"agent printed no JSON.\nstdout={proc.stdout!r}\n"
                             f"stderr={proc.stderr!r}") from None
    return payload, proc.returncode


class AgentTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir=SCRATCH)
        self.save = os.path.join(self.tmp, "campaign.json")
        shutil.copyfile(FIXTURE, self.save)
        self.out = os.path.join(self.tmp, "out.json")
        self.edits = os.path.join(self.tmp, "edits.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_edits(self, edits):
        with open(self.edits, "w", encoding="utf-8") as fh:
            json.dump(edits, fh)
        return self.edits

    def apply_cli(self, edits, *extra):
        self.write_edits(edits)
        return run_cli("apply", self.save, self.edits, "--out", self.out, *extra)

    def load(self, path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def item(self, data, item_type, item_id):
        for row in data["inventory"]["items"]:
            if int(row["item_type"]) == item_type and int(row["item_id"]) == item_id:
                return int(row["count"])
        return None


class CurrencyTest(AgentTestCase):
    def test_add_gil_lapis(self):
        payload, code = self.apply_cli({"currencies": {"gil": 1000, "lapis_free": 100}})
        self.assertEqual(payload["ok"], True, payload)
        self.assertEqual(code, 0)
        data = self.load(self.out)
        self.assertEqual(data["currencies"]["gil"], FIXTURE_GIL + 1000)
        self.assertEqual(data["currencies"]["lapis_free"], FIXTURE_LAPIS + 100)
        # the input copy is untouched: apply writes only --out
        self.assertEqual(self.load(self.save)["currencies"]["gil"], FIXTURE_GIL)

    def test_negative_rejected(self):
        before = sha256(self.save)
        payload, code = self.apply_cli({"currencies": {"gil": -5}})
        self.assertEqual(payload["ok"], False)
        self.assertNotEqual(code, 0)
        self.assertIn("negative", (payload["error"] or "").lower())
        self.assertEqual(sha256(self.save), before)
        self.assertFalse(os.path.exists(self.out))

    def test_non_integer_rejected(self):
        payload, _ = self.apply_cli({"currencies": {"gil": "1000"}})
        self.assertEqual(payload["ok"], False)
        self.assertIn("integer", (payload["error"] or "").lower())

    def test_unknown_currency_rejected(self):
        payload, _ = self.apply_cli({"currencies": {"lapis": 10}})
        self.assertEqual(payload["ok"], False)
        self.assertIn("unknown currency", (payload["error"] or "").lower())


class TicketTest(AgentTestCase):
    def test_ticket_added_new_and_existing(self):
        rows = [{"item_type": 23, "item_id": 180, "count": 5},
                {"item_type": 20, "item_id": 1300000007, "count": 3}]
        payload, code = self.apply_cli({"tickets": rows})
        self.assertEqual(payload["ok"], True, payload)
        data = self.load(self.out)
        self.assertEqual(self.item(data, 23, 180), 9 + 5)
        self.assertEqual(self.item(data, 20, 1300000007), 3)

    def test_ticket_over_cap_rejected(self):
        import player_state
        ps = player_state.PlayerState.load(self.save, slot="campaign")
        with self.assertRaises(ValueError):
            ops_basic.apply_tickets(ps, [{"item_type": 23, "item_id": 50, "count": 100}])
        # nothing written by the CLI either
        before = sha256(self.save)
        payload, _ = self.apply_cli({"tickets": [{"item_type": 23, "item_id": 50, "count": 100}]})
        self.assertEqual(payload["ok"], False)
        self.assertEqual(sha256(self.save), before)

    def test_unspendable_rejected(self):
        import player_state
        ps = player_state.PlayerState.load(self.save, slot="campaign")
        with self.assertRaises(ValueError):
            ops_basic.apply_tickets(ps, [{"item_type": 23, "item_id": 908, "count": 1}])
        payload, _ = self.apply_cli({"tickets": [{"item_type": 23, "item_id": 908, "count": 1}]})
        self.assertEqual(payload["ok"], False)
        self.assertIn("unspendable", (payload["error"] or "").lower())

    def test_unknown_item_rejected(self):
        payload, _ = self.apply_cli({"tickets": [{"item_type": 20, "item_id": 999999999, "count": 1}]})
        self.assertEqual(payload["ok"], False)
        self.assertIn("unknown", (payload["error"] or "").lower())


class SafetyTest(AgentTestCase):
    def test_apply_roundtrip_passes_coherence(self):
        payload, code = self.apply_cli({
            "currencies": {"gil": 500},
            "tickets": [{"item_type": 23, "item_id": 180, "count": 1}],
        }, "--workdir", self.tmp)
        self.assertEqual(payload["ok"], True, payload)
        self.assertEqual(code, 0)
        expected = os.path.join(self.tmp, "expected.json")
        self.assertTrue(os.path.exists(expected))
        check, check_code = run_cli("validate", self.out,
                                    "--baseline", FIXTURE, "--expected", expected)
        self.assertEqual(check["ok"], True, check)
        self.assertEqual(check_code, 0)

    def test_validate_detects_fresh_account(self):
        import player_state
        fresh = os.path.join(self.tmp, "fresh.json")
        with open(fresh, "w", encoding="utf-8") as fh:
            json.dump(player_state.default_state(), fh)
        payload, code = run_cli("validate", fresh, "--baseline", FIXTURE)
        self.assertEqual(payload["ok"], False)
        self.assertNotEqual(code, 0)
        self.assertTrue(any("roster" in issue or "gil" in issue
                            for issue in payload["result"]["issues"]), payload)

    def test_missing_path_refused(self):
        self.write_edits({"currencies": {"gil": 1}})
        payload, code = run_cli("apply", os.path.join(self.tmp, "nope.json"),
                                self.edits, "--out", self.out)
        self.assertEqual(payload["ok"], False)
        self.assertNotEqual(code, 0)
        self.assertIn("does not exist", payload["error"])
        self.assertFalse(os.path.exists(self.out))

    def test_corrupt_json_refused(self):
        broken = os.path.join(self.tmp, "broken.json")
        with open(broken, "w", encoding="utf-8") as fh:
            fh.write("{")
        self.write_edits({"currencies": {"gil": 1}})
        payload, code = run_cli("apply", broken, self.edits, "--out", self.out)
        self.assertEqual(payload["ok"], False)
        self.assertNotEqual(code, 0)
        self.assertIn("JSON", payload["error"])
        self.assertFalse(os.path.exists(self.out))

    def test_empty_edits_refused(self):
        payload, _ = self.apply_cli({})
        self.assertEqual(payload["ok"], False)
        self.assertIn("no edits", (payload["error"] or "").lower())

    def test_expected_mismatch_refused(self):
        payload, code = self.apply_cli({
            "currencies": {"gil": 10},
            "expected": {"currencies": {}, "roster_added": 4},
        })
        self.assertEqual(payload["ok"], False)
        self.assertEqual(code, 2)
        self.assertIn("roster", (payload["error"] or "").lower())
        self.assertFalse(os.path.exists(self.out))


class SummaryTest(AgentTestCase):
    def test_summary_reports_fixture(self):
        payload, code = run_cli("summary", self.save)
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["ok"], True)
        result = payload["result"]
        self.assertEqual(result["gil"], FIXTURE_GIL)
        self.assertEqual(len(result["roster"]), 15)
        self.assertEqual(len(result["vision_cards"]), 1)
        lightning = [u for u in result["roster"] if u["master_id"] == "213000105"]
        self.assertEqual(lightning[0]["name"], "Lightning")
        # The pinned fixture is the PRE-experiment baseline: Lightning is level 1 here.
        self.assertEqual(lightning[0]["level"], 1)
        self.assertEqual(lightning[0]["rarity"], 5)
        self.assertEqual(lightning[0]["lb_level"], 1)


if __name__ == "__main__":
    unittest.main()
