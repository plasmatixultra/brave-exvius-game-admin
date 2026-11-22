"""A docker double for tests: records argv, answers from tests/fixtures."""

import os
import shutil
import tempfile

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


class FakeDocker:
    """Put a `docker` executable on PATH for the duration of a `with` block."""

    def __init__(self, inspect_json="docker_inspect_standard.json"):
        self.dir = tempfile.mkdtemp()
        self.calls = os.path.join(self.dir, "calls")
        self.inspect_json = os.path.join(FIXTURES, inspect_json)
        shutil.copy(os.path.join(FIXTURES, "fake-docker"), os.path.join(self.dir, "docker"))
        os.chmod(os.path.join(self.dir, "docker"), 0o755)
        self.saved = dict(os.environ)

    def __enter__(self):
        os.environ["PATH"] = self.dir + os.pathsep + self.saved["PATH"]
        os.environ["CALLS_FILE"] = self.calls
        os.environ["FAKE_INSPECT_JSON"] = self.inspect_json
        return self

    def __exit__(self, *exc):
        os.environ.clear()
        os.environ.update(self.saved)

    def argv(self):
        if not os.path.exists(self.calls):
            return []
        with open(self.calls, encoding="utf-8") as handle:
            return handle.read().splitlines()
