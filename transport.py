"""The docker CLI. The only place in this app that spawns docker."""

import json
import subprocess

#: How long a plain command (stop, start, inspect) may take.
PLAIN_TIMEOUT = 120


class TransportError(RuntimeError):
    """A docker command failed, timed out, or docker is not installed."""

    def __init__(self, returncode, command, stderr=""):
        self.returncode = returncode
        self.command = list(command) if not isinstance(command, str) else command
        self.stderr = stderr or ""
        super().__init__(str(self))

    def __str__(self):
        shown = self.command if isinstance(self.command, str) else " ".join(self.command)
        detail = self.stderr.strip()
        return "docker failed (%s): %s%s" % (
            self.returncode, shown, ("\n" + detail) if detail else "")


def docker(args, timeout=PLAIN_TIMEOUT, input=None):
    """Run one docker command and return its CompletedProcess.

    stdout/stderr are text. ``input`` is fed to stdin; without it stdin is
    closed, so a command that asks a question fails instead of hanging.
    """
    argv = ["docker", *args]
    try:
        done = subprocess.run(
            argv,
            input=input,
            stdin=subprocess.DEVNULL if input is None else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        raise TransportError(127, argv, "the docker command was not found")
    except subprocess.TimeoutExpired as exc:
        raise TransportError(124, argv, "timed out after %ss" % timeout) from exc
    if done.returncode != 0:
        raise TransportError(done.returncode, argv, done.stderr or done.stdout)
    return done


def inspect(container, timeout=PLAIN_TIMEOUT):
    """The container's own view of itself: mounts, image, state."""
    done = docker(["inspect", container], timeout=timeout)
    try:
        payload = json.loads(done.stdout)
    except ValueError:
        raise TransportError(1, ["inspect", container], "output is not JSON")
    if isinstance(payload, dict):
        payload = [payload]
    if not payload:
        raise TransportError(1, ["inspect", container], "nothing was returned")
    return payload[0]


def running(container):
    return bool((inspect(container).get("State") or {}).get("Running"))


def stop(container):
    docker(["stop", container])


def start(container):
    docker(["start", container])


def status(container, timeout=60):
    """The game's own status line, from inside the running container."""
    done = docker(["exec", container, "python3", "/app/ffbe.py", "status"], timeout=timeout)
    return done.stdout.strip()


def run_in_image(image, mounts, command, timeout=900, env=None, stdin_closed=True):
    """Run ``command`` in a throwaway container of ``image``.

    ``mounts`` is a list of ``(host_path, container_path, "ro"|"rw")``.
    """
    args = ["run", "--rm"]
    for host_path, container_path, mode in mounts:
        args += ["-v", "%s:%s:%s" % (host_path, container_path, mode)]
    for name, value in (env or {}).items():
        args += ["-e", "%s=%s" % (name, value)]
    args += [image, *command]
    return docker(args, timeout=timeout)
