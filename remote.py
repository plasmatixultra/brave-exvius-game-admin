"""ssh/scp transport to the game host. Every other module goes through here."""

import subprocess

import config

SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
TARGET = f"{config.USER}@{config.HOST}"


class RemoteError(RuntimeError):
    """A remote command or copy exited non-zero."""

    def __init__(self, returncode, cmd, stderr):
        self.returncode = returncode
        self.cmd = cmd
        self.stderr = stderr

    def __str__(self):
        return f"remote command failed ({self.returncode}): {self.cmd}\n{self.stderr.strip()}"


def run(cmd, sudo=False, stdin=None, timeout=120):
    """Run `cmd` on the host over ssh and return the CompletedProcess.

    stdout/stderr are text. Raises RemoteError on a non-zero exit.
    With sudo=True the command is prefixed with `sudo -n`, so a password
    prompt fails instead of hanging.
    """
    remote_cmd = f"sudo -n {cmd}" if sudo else cmd
    argv = ["ssh", *SSH_OPTS, TARGET, remote_cmd]
    result = subprocess.run(
        argv,
        input=stdin,
        # Never let a remote command eat the local terminal's stdin.
        stdin=subprocess.DEVNULL if stdin is None else None,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RemoteError(result.returncode, remote_cmd, result.stderr)
    return result


def put(local, remote_path):
    """Copy `local` to `remote_path` on the host with scp; raise RemoteError on failure."""
    argv = ["scp", *SSH_OPTS, local, f"{TARGET}:{remote_path}"]
    result = subprocess.run(argv, capture_output=True, text=True)
    if result.returncode != 0:
        raise RemoteError(result.returncode, " ".join(argv), result.stderr)
