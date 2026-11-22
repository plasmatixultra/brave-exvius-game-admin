"""The engine's own source, exported from the image once per image id.

Reads run on this machine's Python against the engine's real modules, so they
cost ~0.3 s instead of a container start. The copy is cheap to rebuild and
keyed to the image, so a pulled image never runs against stale code.
"""

import os
import shutil
import tempfile

import transport


class EngineError(RuntimeError):
    """The image does not carry the server source we need."""


def cache_root():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "ffbe-admin")


def _image_id(settings):
    """The image the container is actually running, so `:latest` cannot go stale."""
    try:
        return transport.inspect(settings.container).get("Image") or settings.image
    except transport.TransportError:
        return settings.image


def _stamp_matches(path, wanted):
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip() == wanted
    except OSError:
        return False


def ensure(settings, cache_dir=None, timeout=1200):
    """Return a directory holding the image's ``/app/server``, exporting if needed."""
    cache_dir = cache_dir or cache_root()
    target = os.path.join(cache_dir, "engine")
    stamp = os.path.join(target, ".image-id")
    wanted = _image_id(settings)
    if os.path.isfile(os.path.join(target, "player_state.py")) and _stamp_matches(stamp, wanted):
        return target

    os.makedirs(cache_dir, exist_ok=True)
    staging = tempfile.mkdtemp(prefix="engine-", dir=cache_dir)
    try:
        try:
            transport.run_in_image(
                settings.image, [(staging, "/export", "rw")],
                ["cp", "-a", "/app/server/.", "/export/"], timeout=timeout)
        except transport.TransportError as error:
            raise EngineError(
                "cannot export /app/server from %s: %s"
                % (settings.image, (error.stderr or str(error)).strip().splitlines()[0]))
        if not os.path.isfile(os.path.join(staging, "player_state.py")):
            raise EngineError(
                "the image %s has no /app/server/player_state.py; is it the game image?"
                % settings.image)
        with open(os.path.join(staging, ".image-id"), "w", encoding="utf-8") as handle:
            handle.write(wanted)
        if os.path.isdir(target):
            os.replace(target, target + ".old")      # rename is atomic; the tree is not
        os.replace(staging, target)
        shutil.rmtree(target + ".old", ignore_errors=True)
        return target
    finally:
        shutil.rmtree(staging, ignore_errors=True)
