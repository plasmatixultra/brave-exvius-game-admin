"""Where the game lives: container, save, backups and data, read from docker.

Nothing here names a machine. The container is asked for its own mounts; a
user who cannot be asked (no docker yet, or a stopped daemon) can answer with
``admin.toml`` or the environment instead.
"""

import json
import os
import subprocess

CONTAINER_SERVER_ROOT = "/srv/ffbe/server-root"
CONTAINER_DATA = "/srv/ffbe/data"
CONTAINER_SAVE = CONTAINER_SERVER_ROOT + "/state/campaign.json"

DEFAULT_CONTAINER = "ffbe"
DEFAULT_IMAGE = "ffbe-ios-server:local"
DEFAULT_BIND = "127.0.0.1"
DEFAULT_PORT = 8750
DEFAULT_BACKUPS = "state/backups"

#: Setting name -> environment variable. One key space everywhere: the
#: environment names are also what ``from_inspect`` accepts as overrides.
ENV_KEYS = {
    "container": "FFBE_CONTAINER",
    "image": "FFBE_IMAGE",
    "server_dir": "FFBE_SERVER_DIR",
    "data_dir": "FFBE_DATA_DIR",
    "bind": "ADMIN_BIND",
    "port": "ADMIN_PORT",
}

#: ``admin.toml`` keys, under ``[admin]``, mapped to the same names.
TOML_KEYS = {
    "container": "container",
    "image": "image",
    "server_dir": "server_dir",
    "data_dir": "data_dir",
    "bind": "bind",
    "port": "port",
}

CONFIG_ENV = "ADMIN_CONFIG"
DEFAULT_CONFIG_PATH = "admin.toml"


class SettingsError(RuntimeError):
    """Raised when the game cannot be located, or an override is unknown."""


def _overrides_to_settings(overrides):
    """Accept both ``{"FFBE_SERVER_DIR": …}`` and ``{"server_dir": …}``."""
    known = set(ENV_KEYS.values()) | set(ENV_KEYS)
    unknown = sorted(set(overrides) - known)
    if unknown:
        raise SettingsError("unknown setting: " + ", ".join(unknown))
    resolved = {}
    for name, env in ENV_KEYS.items():
        value = overrides.get(env, overrides.get(name))
        if value not in (None, ""):
            resolved[name] = value
    if "port" in resolved:
        try:
            resolved["port"] = int(resolved["port"])
        except (TypeError, ValueError):
            raise SettingsError("port must be a number, not %r" % (resolved["port"],))
    return resolved


def _mount_host(payload, destination):
    for mount in payload.get("Mounts") or []:
        if mount.get("Destination") == destination:
            source = mount.get("Source")
            if not source:
                raise SettingsError("%s is mounted but has no host path" % destination)
            return os.path.realpath(source)
    raise SettingsError(
        "%s is not mounted; is this the game container?" % destination)


def from_inspect(payload, overrides=None):
    """Build :class:`Settings` from one ``docker inspect`` element (or the list)."""
    if isinstance(payload, list):
        if not payload:
            raise SettingsError("docker inspect returned nothing")
        payload = payload[0]
    if not payload:
        raise SettingsError("docker inspect returned nothing")
    found = {
        "container": str(payload.get("Name", "")).lstrip("/") or DEFAULT_CONTAINER,
        "image": (payload.get("Config") or {}).get("Image") or DEFAULT_IMAGE,
        "server_dir": _mount_host(payload, CONTAINER_SERVER_ROOT),
        "data_dir": _mount_host(payload, CONTAINER_DATA),
    }
    found.update(_overrides_to_settings(overrides or {}))
    return Settings(
        container=found["container"],
        image=found["image"],
        server_dir=found["server_dir"],
        data_dir=found["data_dir"],
        save=os.path.join(found["server_dir"], "state", "campaign.json"),
        backups=os.path.join(found["server_dir"], DEFAULT_BACKUPS),
        bind=found.get("bind", DEFAULT_BIND),
        port=found.get("port", DEFAULT_PORT),
    )


class Settings:
    """Resolved locations. Frozen so nothing rewrites them mid-apply."""

    __slots__ = ("container", "image", "server_dir", "data_dir",
                 "save", "backups", "bind", "port")

    def __init__(self, container, image, server_dir, data_dir, save, backups, bind, port):
        for name in self.__slots__:
            object.__setattr__(self, name, locals()[name])

    def __setattr__(self, name, value):
        raise AttributeError("Settings is frozen")

    def __eq__(self, other):
        return isinstance(other, Settings) and all(
            getattr(self, name) == getattr(other, name) for name in self.__slots__)

    def __repr__(self):
        return "Settings(%s)" % ", ".join(
            "%s=%r" % (name, getattr(self, name)) for name in self.__slots__)

    def as_dict(self):
        return {name: getattr(self, name) for name in self.__slots__}


def detect(container=DEFAULT_CONTAINER, timeout=20):
    """Ask docker where the game's directories are."""
    import transport

    try:
        payload = transport.inspect(container, timeout=timeout)
    except transport.TransportError as error:
        detail = error.stderr.strip().splitlines()
        first = detail[0] if detail else ""
        if "no such object" in first.lower() or "no such container" in first.lower():
            raise SettingsError(
                "container %s was not found. Is the game running, and is %s the right name?"
                % (container, container))
        raise SettingsError("cannot read container %s: %s" % (container, first or "docker failed"))
    return from_inspect(payload).as_dict()


def _read_toml(path):
    try:
        import tomllib
    except ImportError:                                # pragma: no cover - py<3.11
        raise SettingsError("Python 3.11 or newer is needed to read %s" % path)
    try:
        with open(path, "rb") as handle:
            document = tomllib.load(handle)
    except FileNotFoundError:
        return {}
    except OSError as error:
        raise SettingsError("cannot read %s: %s" % (path, error))
    section = document.get("admin", document)
    return {TOML_KEYS[key]: section[key] for key in TOML_KEYS if key in section}


def load(env=None, container=None):
    """detect() < admin.toml < environment. Detection is optional if both
    directories are configured by hand, which is what a stopped container
    on a NAS looks like."""
    env = dict(os.environ if env is None else env)
    path = env.get(CONFIG_ENV) or DEFAULT_CONFIG_PATH
    from_file = _read_toml(path)
    from_env = _overrides_to_settings({k: v for k, v in env.items() if k in ENV_KEYS.values()})

    try:
        base = detect(container or from_env.get("container") or from_file.get("container")
                      or env.get(ENV_KEYS["container"]) or DEFAULT_CONTAINER)
    except SettingsError as error:
        merged = dict(from_file)
        merged.update(from_env)
        if "server_dir" not in merged or "data_dir" not in merged:
            raise SettingsError(
                "%s To run without detection, set %s and %s in %s."
                % (error, ENV_KEYS["server_dir"], ENV_KEYS["data_dir"], path))
        base = {"container": merged.get("container", DEFAULT_CONTAINER),
                "image": merged.get("image", DEFAULT_IMAGE),
                "server_dir": os.path.realpath(merged["server_dir"]),
                "data_dir": os.path.realpath(merged["data_dir"])}

    overrides = dict(from_file)
    overrides.update(from_env)
    return from_inspect([{"Name": base["container"],
                          "Config": {"Image": base["image"]},
                          "Mounts": [
                              {"Destination": CONTAINER_SERVER_ROOT, "Source": base["server_dir"]},
                              {"Destination": CONTAINER_DATA, "Source": base["data_dir"]}]}],
                        overrides)


def ensure_directories(settings):
    """Create the backup directory; the save's own directory must already exist."""
    os.makedirs(settings.backups, exist_ok=True)
    return settings.backups
