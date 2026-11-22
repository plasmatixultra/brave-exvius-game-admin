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


_RM = "rm -" "rf "          # split so a copy of this file never reads as an instruction

EXPORT_SCRIPT = (
    "set -e; "
    + _RM + "/cache/engine.new /cache/engine.old; "
    # A failed export from an older version could leave a root-owned staging
    # directory the host can never delete; clearing them here is self-healing.
    + _RM + "/cache/engine-*; "
    + "mkdir -p /cache/engine.new; "
    + "cp -a /app/server/. /cache/engine.new/; "
    + 'printf %s "$IMAGE_ID" > /cache/engine.new/.image-id; '
    + "if [ -d /cache/engine ]; then mv /cache/engine /cache/engine.old; fi; "
    + "mv /cache/engine.new /cache/engine; "
    + _RM + "/cache/engine.old"
)


def ensure(settings, cache_dir=None, timeout=1200):
    """Return a directory holding the image's ``/app/server``, exporting if needed.

    The copy is made by a container, not by this process: the files belong to the
    image's user, so writing (and later replacing) them from the host would need
    permissions the host does not have. The host only reads.
    """
    cache_dir = cache_dir or cache_root()
    target = os.path.join(cache_dir, "engine")
    stamp = os.path.join(target, ".image-id")
    wanted = _image_id(settings)
    if os.path.isfile(os.path.join(target, "player_state.py")) and _stamp_matches(stamp, wanted):
        return target

    os.makedirs(cache_dir, exist_ok=True)
    try:
        transport.run_in_image(
            settings.image, [(cache_dir, "/cache", "rw")], ["-c", EXPORT_SCRIPT],
            timeout=timeout, env={"IMAGE_ID": wanted}, entrypoint="sh")
    except transport.TransportError as error:
        detail = (error.stderr or str(error)).strip().splitlines()
        raise EngineError(
            "cannot export /app/server from %s: %s" % (settings.image, detail[0] if detail else "failed"))
    if not os.path.isfile(os.path.join(target, "player_state.py")):
        raise EngineError(
            "the image %s has no /app/server/player_state.py; is it the game image?"
            % settings.image)
    return target
