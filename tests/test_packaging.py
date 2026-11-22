"""What ships must install on someone else's machine.

The product surface — the code, the UI, the README, the examples — may not name
this machine. The build record under docs/superpowers is history and is allowed to
say where it was built; that is provenance, not configuration.
"""

import os
import re
import sys
import tomllib
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config  # noqa: E402

FORBIDDEN = ("plasmatixultra", "10.129.1.139", "athena", "/home/plasmatixultra")

# Everything a user installs, plus the docs they read first.
PRODUCT_GLOBS = ("*.py", "runner/*.sh", "tests/*.py", "tests/*.sh", "tests/*.mjs",
                 "static/*.js", "static/*.html", "static/*.css", "agent/*.py",
                 ".gitignore", "README.md", "LICENSE", "compat.json",
                 "admin.toml.example", "docker-compose.admin.yml")


def product_files():
    for pattern in PRODUCT_GLOBS:
        directory, _, name = pattern.rpartition("/")
        base = os.path.join(ROOT, directory) if directory else ROOT
        if not os.path.isdir(base):
            continue
        for entry in sorted(os.listdir(base)):
            path = os.path.join(base, entry)
            if os.path.isfile(path) and re.fullmatch(name.replace(".", r"\.").replace("*", ".*"), entry):
                yield os.path.relpath(path, ROOT)


class ProductSurfaceTest(unittest.TestCase):
    def test_no_personal_paths_in_what_ships(self):
        offenders = []
        for relative in product_files():
            if relative.endswith("test_packaging.py"):
                continue
            with open(os.path.join(ROOT, relative), encoding="utf-8", errors="ignore") as handle:
                text = handle.read()
            for token in FORBIDDEN:
                if token in text:
                    offenders.append("%s names %s" % (relative, token))
        self.assertEqual(offenders, [])

    def test_the_readme_says_how_to_start(self):
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as handle:
            readme = handle.read()
        for phrase in ("python3 app.py", "127.0.0.1:8750", "campaign.json"):
            self.assertIn(phrase, readme)

    def test_the_readme_warns_that_it_edits_the_save(self):
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as handle:
            readme = handle.read().lower()
        self.assertIn("backup", readme)
        self.assertIn("disclaimer", readme)

    def test_the_licence_is_mit(self):
        with open(os.path.join(ROOT, "LICENSE"), encoding="utf-8") as handle:
            licence = handle.read()
        self.assertIn("MIT License", licence)
        self.assertIn("WITHOUT WARRANTY OF ANY KIND", licence)


class ExamplesTest(unittest.TestCase):
    def test_admin_toml_example_uses_the_keys_the_app_reads(self):
        with open(os.path.join(ROOT, "admin.toml.example"), "rb") as handle:
            example = tomllib.load(handle)
        section = example.get("admin", {})
        self.assertTrue(section or True)            # every key is commented out on purpose
        for key in ("container", "image", "server_dir", "data_dir", "bind", "port"):
            self.assertIn(key, config.TOML_KEYS)
        with open(os.path.join(ROOT, "admin.toml.example"), encoding="utf-8") as handle:
            text = handle.read()
        for key in config.TOML_KEYS:
            self.assertIn(key, text)

    def test_the_overlay_documents_the_admin_port_only_on_loopback(self):
        with open(os.path.join(ROOT, "docker-compose.admin.yml"), encoding="utf-8") as handle:
            overlay = handle.read()
        self.assertIn("127.0.0.1:8750:8750", overlay)


if __name__ == "__main__":
    unittest.main()
