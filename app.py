#!/usr/bin/env python3
"""FFBE Game Admin — the local page and its JSON API.

    python3 app.py            → http://127.0.0.1:8750

Loopback only, no auth, one user. Every write goes through `pipeline.apply`, which runs
`remote/apply.sh` on athena under a lock and a trap; this file never edits a save itself.
"""

import json
import os
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import localdata
import pipeline

ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(ROOT, "static")
UNIT_CACHE = os.path.join(STATIC, "assets", "units")
HOST = "127.0.0.1"
PORT = 8750
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
CATALOG_TTL = 600

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css",
    ".js": "application/javascript",
    ".png": "image/png",
    ".ttf": "font/ttf",
}

_CATALOG = {"at": 0.0, "value": None}


# --------------------------------------------------------------------------- the outside world
# Module-level callables so the tests can replace them wholesale.


def agent(op, edits=None, flags=None):
    """Run one agent op against the CACHED save (localdata) and return its result.

    Reads never touch docker: the engine comes out of the image once, the save and the
    tables are read from the game's own directories (see localdata.py). Only
    `pipeline_apply` stops anything, and only to write.
    """
    return localdata.agent(op, edits, flags)


def pipeline_apply(edits):
    """Run the whole apply pipeline (raises nothing; returns its dict)."""
    return pipeline.apply(edits)


def fetch_unit_art(master_id):
    """The server's cached per-form sprite, or None. A plain read of the game's own file."""
    path = localdata.server_path("ios-cache", "notice-art", "unit_ills_%s.png" % master_id)
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError:
        return None


def catalog():
    now = time.time()
    if _CATALOG["value"] is None or now - _CATALOG["at"] > CATALOG_TTL:
        _CATALOG["value"] = agent("catalog")
        _CATALOG["at"] = now
    return _CATALOG["value"]


# ---------------------------------------------------------------------------------- the handler


class Handler(BaseHTTPRequestHandler):
    server_version = "FFBEAdmin/1.0"

    def log_message(self, fmt, *args):  # quiet: the page shows its own log
        pass

    # ---- plumbing
    def _send(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, status=200):
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json")

    def _error(self, message, status=502):
        self._json({"ok": False, "error": str(message)}, status)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        return json.loads(raw.decode("utf-8")) if raw else {}

    # ---- GET
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        try:
            if path in ("/", "/index.html"):
                return self._static_file("index.html")
            if path.startswith("/static/"):
                return self._static_file(path[len("/static/"):])
            if path == "/api/summary":
                if "refresh=1" in (self.path.split("?", 1) + [""])[1]:
                    localdata.refresh_save(force=True)
                return self._json(agent("summary"))
            if path == "/api/catalog":
                return self._json(catalog())
            if path == "/api/recipe":
                return self._recipe()
            if path == "/api/questline/regions":
                return self._json(agent("questline"))
            if path == "/api/questline":
                params = self._query()
                if params.get("world") is not None:
                    return self._json(agent("questline", flags={"world": params["world"]}))
                if params.get("region"):
                    if not str(params["region"]).isdigit():
                        return self._error("region must be a numeric id", 400)
                    if params.get("area") and not str(params["area"]).isdigit():
                        return self._error("area must be a numeric id", 400)
                    flags = {"region": params["region"]}
                    if params.get("area"):
                        flags["area"] = params["area"]
                    return self._json(agent("questline", flags=flags))
                return self._error("questline needs region or world", 400)
            if path == "/api/awaken":
                return self._awaken()
            if path == "/api/abilities":
                return self._abilities()
            if path.startswith("/api/unit-art/"):
                return self._unit_art(path)
            return self._error("not found", 404)
        except RuntimeError as exc:
            return self._error(exc)
        except Exception as exc:  # noqa: BLE001 - the page must always get an answer
            return self._error(f"{type(exc).__name__}: {exc}")

    def _recipe(self):
        params = self._query()
        table, row_id = params.get("table"), params.get("id")
        if not table or not row_id:
            return self._error("recipe needs table and id", 400)
        return self._json(agent("recipe", flags={"table": table, "id": row_id}))

    def _awaken(self):
        params = self._query()
        source, target = params.get("from"), params.get("to")
        if not source or not target:
            return self._error("awaken needs from and to", 400)
        return self._json(agent("awaken", flags={"from": source, "to": target}))

    def _abilities(self):
        params = self._query()
        master = params.get("master")
        if not master:
            return self._error("abilities needs master", 400)
        return self._json(agent("abilities", flags={"master": master}))

    def _query(self):
        """Percent-decoded query params. `parse_qs` matters: `id=213000117%3A1` is a colon in a value."""
        return {key: values[0] for key, values in
                urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).items()}

    def _static_file(self, name):
        path = os.path.normpath(os.path.join(STATIC, name))
        if not path.startswith(STATIC + os.sep) or not os.path.isfile(path):
            return self._error("not found", 404)
        extension = os.path.splitext(path)[1].lower()
        content_type = CONTENT_TYPES.get(extension)
        if content_type is None:
            return self._error("unsupported file type", 404)
        with open(path, "rb") as fh:
            self._send(200, fh.read(), content_type)

    def _unit_art(self, path):
        master_id = path.rsplit("/", 1)[-1]
        if master_id.endswith(".png"):
            master_id = master_id[:-4]
        if not master_id.isdigit():
            return self._error("bad master id", 404)
        cached = os.path.join(UNIT_CACHE, master_id + ".png")
        if os.path.isfile(cached):
            with open(cached, "rb") as fh:
                return self._send(200, fh.read(), "image/png")
        data = fetch_unit_art(master_id)
        if not data or not data.startswith(PNG_MAGIC):
            return self._error("no art", 404)
        os.makedirs(UNIT_CACHE, exist_ok=True)
        with open(cached, "wb") as fh:
            fh.write(data)
        self._send(200, data, "image/png")

    # ---- POST
    def _cross_origin(self):
        """Writes are same-origin only.

        The app has no auth on purpose (loopback, one user), but "one user" does not cover the
        user's OTHER browser tabs: any page can POST to 127.0.0.1 and a form post cannot be refused
        by CORS. Fetch Metadata is forwarded by the keyed proxy, so it is the reliable signal;
        Origin/Host is the fallback for browsers that predate it.
        """
        site = (self.headers.get("Sec-Fetch-Site") or "").lower()
        if site in ("cross-site", "same-site"):
            # `same-site` is another port on 127.0.0.1 — a different application, not this page.
            return True
        if site:
            return False                    # same-origin / none
        origin = self.headers.get("Origin")
        if not origin:
            return False                    # curl, the tests, a browser with no Fetch Metadata
        return urllib.parse.urlsplit(origin).netloc != (self.headers.get("Host") or "")

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if self._cross_origin():
            return self._error("cross-origin writes are refused", 403)
        try:
            body = self._body()
        except ValueError:
            return self._error("body is not JSON", 400)
        try:
            if path == "/api/preview":
                spec = body.get("spec") if isinstance(body, dict) else None
                if not isinstance(spec, dict):
                    return self._error("preview needs a spec object", 400)
                return self._json(agent("preview", {"unit_spec": spec}))
            if path == "/api/progress/preview":
                progress = body.get("progress") if isinstance(body, dict) else None
                if not isinstance(progress, dict):
                    return self._error("progress preview needs a progress object", 400)
                result = agent("preview", {"progress": progress})
                group = (result.get("groups") or {}).get("progress") or {}
                expected = {
                    "currencies": {name: row.get("delta", 0)
                                   for name, row in (result.get("currencies") or {}).items()},
                    "roster_added": result.get("roster_added", 0),
                    "extra": {key: group[key] for key in ("cleared", "quests", "espers_granted", "gates")
                              if key in group},
                }
                return self._json({"expected": expected, "report": group})
            if path == "/api/apply":
                edits = body.get("edits") if isinstance(body, dict) else None
                if not isinstance(edits, dict):
                    return self._error("apply needs an edits object", 400)
                result = pipeline_apply(edits)
                if result.get("ok"):
                    localdata.refresh_save(force=True)   # the live file just changed
                return self._json({"ok": bool(result.get("ok")), "log": result.get("log") or [],
                                   "backup": result.get("backup"), "error": result.get("error"),
                                   "status": "ok" if result.get("ok") else "failed"})
            return self._error("not found", 404)
        except RuntimeError as exc:
            return self._error(exc)
        except Exception as exc:  # noqa: BLE001
            return self._error(f"{type(exc).__name__}: {exc}")


def create_server(port=PORT, host=HOST):
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def main(argv=None):
    argv = argv or sys.argv[1:]
    port = int(argv[0]) if argv else PORT
    server = create_server(port)
    host, bound = server.server_address[0], server.server_address[1]
    print(f"FFBE Game Admin on http://{host}:{bound}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
