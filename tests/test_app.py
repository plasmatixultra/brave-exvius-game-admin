"""The web app: routing, content types, the unit-art cache, and the loopback binding.

The agent and the pipeline are faked here — nothing in this file touches athena, a save, or docker.

    python3 -m unittest tests.test_app -v
"""

import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)

import app  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"fake-body"


class AppTest(unittest.TestCase):
    # see tests/test_cache.py for the cache race tests
    def setUp(self):
        self.calls = []
        self.summary = {"gil": 2980094, "lapis_free": 2208, "lapis_paid": 0,
                        "roster": [{"instance_id": "1", "master_id": "213000105",
                                    "name": "Lightning", "level": 5, "rarity": 5,
                                    "nv_ex_level": 0, "lb_level": 1, "trust": 0, "super_trust": 0}],
                        "vision_cards": [{"user_vc_id": "9", "vc_id": "100000201",
                                          "name": "New Journey", "level": 1, "cap": 10}],
                        "materials_held": {}, "tickets_held": {}, "exp_points": 3500000}
        self.catalog = {"tickets": [], "units": [], "awaken_chain": {}, "vision_cards": [],
                        "materials": [], "points": [], "xp_units": []}
        self.fetch_calls = []
        self.fetch_result = PNG
        self.patches = [
            mock.patch.object(app, "agent", side_effect=self.fake_agent),
            mock.patch.object(app, "pipeline_apply", side_effect=self.fake_apply),
            mock.patch.object(app, "fetch_unit_art", side_effect=self.fake_fetch),
        ]
        for patch in self.patches:
            patch.start()
        self.tmp_units = tempfile.mkdtemp(prefix="unit-art-")
        self.patches.append(mock.patch.object(app, "UNIT_CACHE", self.tmp_units))
        self.patches[-1].start()
        app._CATALOG["value"] = None
        app._CATALOG["at"] = 0.0
        self.server = app.create_server(0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        for patch in self.patches:
            patch.stop()

    # ---- fakes
    def fake_agent(self, op, edits=None, flags=None):
        self.calls.append((op, edits, flags))
        if op == "summary":
            return self.summary
        if op == "catalog":
            return self.catalog
        if op == "preview" and edits and "progress" in edits:
            return {"currencies": {"gil": {"before": 0, "after": 736, "delta": 736},
                                   "lapis_free": {"before": 0, "after": 500, "delta": 500},
                                   "lapis_paid": {"before": 0, "after": 0, "delta": 0}},
                    "roster_added": 0,
                    "groups": {"progress": {"cleared": 9, "quests": 2, "espers_granted": 1, "gates": 0}}}
        if op == "preview":
            return {"unit": {"stats": [1, 2, 3, 4, 5, 6], "max_level": 80, "lb_cap": 20,
                             "trust_cap": 1000, "super_trust_cap": 1000}}
        if op == "recipe":
            return {"materials": [], "gil": 0, "table": "sublimation", "row_id": "1"}
        if op == "abilities":
            return {"recipes": []}
        if op == "questline":
            return {"regions": [{"region_id": 22, "name": "R", "arc": "The Beyond", "dungeon_count": 17}],
                    "flags": flags}
        if op == "awaken":
            return {"from": "100004604", "to": "100004606", "steps": [], "materials": [], "gil": 0}
        raise AssertionError("unexpected op " + op)

    def fake_apply(self, edits):
        self.calls.append(("apply", edits, None))
        return {"ok": True, "backup": "/b/campaign-1.json", "log": ["apply complete"], "error": None}

    def fake_fetch(self, master_id):
        self.fetch_calls.append(master_id)
        return self.fetch_result

    # ---- helpers
    def get(self, path):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.request("GET", path)
        response = connection.getresponse()
        body = response.read()
        connection.close()
        return response.status, response.getheader("Content-Type"), body

    def post(self, path, payload, raw=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        body = raw if raw is not None else json.dumps(payload)
        all_headers = {"Content-Type": "application/json"}
        all_headers.update(headers or {})
        connection.request("POST", path, body=body, headers=all_headers)
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response.status, json.loads(data) if data else None

    # ---- tests
    def test_index_served(self):
        status, content_type, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", content_type)
        text = body.decode("utf-8")
        for view in ("grants", "units", "kit", "ability", "vision", "progress", "log"):
            self.assertIn('data-view="%s"' % view, text)
        self.assertEqual(text.count('<section class="view'), 7)
        self.assertIn('id="cart"', text)
        self.assertIn('id="apply"', text)

    def test_static_css_and_font(self):
        status, content_type, _ = self.get("/static/app.css")
        self.assertEqual((status, content_type), (200, "text/css"))
        status, content_type, body = self.get("/static/assets/BarlowCondensed-Bold.ttf")
        self.assertEqual((status, content_type), (200, "font/ttf"))
        self.assertGreater(len(body), 1000)

    def test_wordmark_served(self):
        status, content_type, body = self.get("/static/assets/ffbe-wordmark.png")
        self.assertEqual((status, content_type), (200, "image/png"))
        self.assertTrue(body.startswith(b"\x89PNG"))

    def test_unit_art_cached(self):
        status, content_type, body = self.get("/api/unit-art/213000105.png")
        self.assertEqual((status, content_type), (200, "image/png"))
        self.assertEqual(body, PNG)
        status, _, body = self.get("/api/unit-art/213000105.png")
        self.assertEqual((status, body), (200, PNG))
        self.assertEqual(self.fetch_calls, ["213000105"])   # served from the cache the second time

    def test_unit_art_missing_404(self):
        self.fetch_result = b""
        status, _, _ = self.get("/api/unit-art/999000001.png")
        self.assertEqual(status, 404)
        self.assertFalse(os.path.exists(os.path.join(self.tmp_units, "999000001.png")))

    def test_apply_rejects_non_json(self):
        status, payload = self.post("/api/apply", None, raw="not json{")
        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])

    def test_binds_loopback_only(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")

    def test_catalog_cached(self):
        first = self.get("/api/catalog")[2]
        second = self.get("/api/catalog")[2]
        self.assertEqual(first, second)
        self.assertEqual([call[0] for call in self.calls].count("catalog"), 1)

    def test_apply_returns_pipeline_result(self):
        status, payload = self.post("/api/apply", {"edits": {"currencies": {"gil": 10}}})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["log"], ["apply complete"])
        self.assertEqual(self.calls[-1], ("apply", {"currencies": {"gil": 10}}, None))

    def test_summary_route(self):
        status, payload = self.post("/api/preview", {"spec": {"master_id": "213000105", "level": 5}})
        self.assertEqual(status, 200)
        self.assertEqual(payload["unit"]["stats"], [1, 2, 3, 4, 5, 6])
        self.assertEqual(self.calls[-1][0], "preview")
        self.assertEqual(self.calls[-1][1], {"unit_spec": {"master_id": "213000105", "level": 5}})

    def test_unknown_path_404(self):
        self.assertEqual(self.get("/nope")[0], 404)

    def test_static_traversal_404(self):
        self.assertEqual(self.get("/static/../app.py")[0], 404)

    def test_cross_origin_writes_are_refused(self):
        """A hostile page must not be able to apply edits to the save through loopback."""
        status, _ = self.post("/api/apply", {"edits": {"currencies": {"gil": 1}}},
                              headers={"Origin": "http://evil.example"})
        self.assertEqual(status, 403)
        self.assertEqual(self.calls, [])
        status, _ = self.post("/api/apply", {"edits": {"currencies": {"gil": 1}}},
                              headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 403)
        self.assertEqual(self.calls, [])

    def test_same_origin_writes_are_accepted(self):
        """The keyed proxy rewrites Host, so Sec-Fetch-Site (forwarded) is what must be trusted."""
        status, _ = self.post("/api/apply", {"edits": {"currencies": {"gil": 1}}},
                              headers={"Sec-Fetch-Site": "same-origin",
                                       "Origin": "http://10.129.1.124:8751"})
        self.assertEqual(status, 200)
        self.assertEqual(len(self.calls), 1)

    def test_index_has_progress_view(self):
        status, _, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b'data-view="progress"', body)

    def test_progress_ids_present(self):
        status, _, body = self.get("/")
        text = body.decode()
        for element_id in ("region-list", "area-list", "side-list", "plan-summary",
                           "plan-rewards", "checkpoint-apply", "side-apply", "sum-side"):
            self.assertIn('id="%s"' % element_id, text)

    def test_rail_has_progress_entry(self):
        status, _, body = self.get("/")
        self.assertIn(b'<a data-view="progress">', body)

    def test_view_count_is_seven(self):
        status, _, body = self.get("/")
        sections = body.decode().count('<section class="view')
        self.assertEqual(sections, 7, "six from project one plus the Progress view")

    def test_questline_regions(self):
        status, _, body = self.get("/api/questline/regions")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["regions"][0]["region_id"], 22)

    def test_questline_plan(self):
        status, _, body = self.get("/api/questline?region=22&area=2210")
        self.assertEqual(status, 200)
        self.assertEqual(self.calls[-1][2], {"region": "22", "area": "2210"})

    def test_questline_side(self):
        status, _, body = self.get("/api/questline?world=1")
        self.assertEqual(status, 200)
        self.assertEqual(self.calls[-1][2], {"world": "1"})

    def test_progress_preview_declares_the_side_rewards(self):
        """The side cart cannot promise what it does not know: this is where it finds out."""
        side = {"1": {"quests": ["1001001"], "dungeons": [{"dungeon_key": "11151", "grant_esper": True}]}}
        status, payload = self.post("/api/progress/preview", {"progress": {"checkpoint": None, "side": side}})
        self.assertEqual(status, 200)
        self.assertEqual(payload["expected"]["currencies"]["lapis_free"], 500)
        self.assertEqual(payload["expected"]["currencies"]["gil"], 736)
        self.assertEqual(payload["expected"]["extra"]["cleared"], 9)
        self.assertEqual(self.calls[-1][0], "preview")
        self.assertIn("progress", self.calls[-1][1])

    def test_same_site_writes_are_refused(self):
        """Another loopback port is a different application, not this page."""
        status, _ = self.post("/api/apply", {"edits": {"currencies": {"gil": 1}}},
                              headers={"Sec-Fetch-Site": "same-site"})
        self.assertEqual(status, 403)

    def test_bad_region_is_400(self):
        status, _, _ = self.get("/api/questline?region=abc")
        self.assertEqual(status, 400)

    def test_progress_preview_needs_a_progress_object(self):
        status, _ = self.post("/api/progress/preview", {"progress": "nope"})
        self.assertEqual(status, 400)

    def test_bad_area_is_400(self):
        status, _, _ = self.get("/api/questline?region=22&area=abc")
        self.assertEqual(status, 400)
        self.assertEqual(self.calls, [])

    def test_apply_accepts_checkpoint(self):
        edits = {"progress": {"checkpoint": {"region": 22, "area": "2210"}},
                 "expected": {"currencies": {"lapis_free": 1}, "roster_added": 0}}
        status, body = self.post("/api/apply", {"edits": edits})
        self.assertEqual(status, 200)
        self.assertEqual(self.calls[-1][1]["progress"]["checkpoint"]["area"], "2210")

    def test_apply_accepts_side_two_worlds_keep_both(self):
        edits = {"progress": {"checkpoint": None, "side": {
            "1": {"quests": ["1001001"], "dungeons": [{"dungeon_key": "11151", "grant_esper": True}]},
            "3": {"quests": [], "dungeons": [{"dungeon_key": "113151", "grant_esper": False}]}}}}
        status, _ = self.post("/api/apply", {"edits": edits})
        self.assertEqual(status, 200)
        side = self.calls[-1][1]["progress"]["side"]
        self.assertEqual(sorted(side), ["1", "3"], "both worlds survive the cart")

    def test_recipe_route_decodes_encoded_ids(self):
        """`?id=213000117%3A1` must reach the agent as `213000117:1` (a colon, not %3A)."""
        status, _, _ = self.get("/api/recipe?table=ex_class_up&id=213000117%3A1")
        self.assertEqual(status, 200)
        self.assertEqual(self.calls[-1][2], {"table": "ex_class_up", "id": "213000117:1"})

    def test_awaken_route(self):
        status, _, body = self.get("/api/awaken?from=100004604&to=100004606")
        self.assertEqual(status, 200)
        self.assertEqual(self.calls[-1][0], "awaken")

    def test_recipe_and_abilities_routes(self):
        status, _, body = self.get("/api/recipe?table=sublimation&id=1")
        self.assertEqual(status, 200)
        self.assertIn("materials", json.loads(body))
        status, _, body = self.get("/api/abilities?master=213000105")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["recipes"], [])
        self.assertEqual(self.get("/api/recipe")[0], 400)


if __name__ == "__main__":
    unittest.main()
