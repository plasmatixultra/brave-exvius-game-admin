"""ssh/scp transport to the game host. Every other module goes through here."""

import subprocess

import config

SSH_OPTS = [
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=10",
    "-o", "ServerAliveInterval=15",
    "-o", "ServerAliveCountMax=3",
]
TARGET = f"{config.USER}@{config.HOST}"


class RemoteError(RuntimeError):
    """A remote command or copy failed, or timed out."""

    def __init__(self, returncode, cmd, stderr):
        self.returncode = returncode
        self.cmd = cmd
        self.stderr = stderr

    def __str__(self):
        return f"remote command failed ({self.returncode}): {self.cmd}\n{self.stderr.strip()}"


def run(cmd, sudo=False, stdin=None, timeout=120):
    """Run `cmd` on the host over ssh and return the CompletedProcess.

    stdout/stderr are text. Raises RemoteError on a non-zero exit, and on a
    timeout (returncode 124) rather than leaking subprocess.TimeoutExpired.
    With sudo=True the command is prefixed with `sudo -n`, so a password
    prompt fails instead of hanging.
    """
    remote_cmd = f"sudo -n {cmd}" if sudo else cmd
    argv = ["ssh", *SSH_OPTS, TARGET, remote_cmd]
    try:
        result = subprocess.run(
            argv,
            input=stdin,
            # Never let a remote command eat the local terminal's stdin.
            stdin=subprocess.DEVNULL if stdin is None else None,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RemoteError(124, remote_cmd, f"timed out after {timeout}s") from exc
    if result.returncode != 0:
        raise RemoteError(result.returncode, remote_cmd, result.stderr)
    return result


def put(local, remote_path):
    """Copy `local` to `remote_path` on the host with scp.

    Raises RemoteError on failure, or with returncode 124 if the transfer
    stalls past its own timeout.
    """
    argv = ["scp", *SSH_OPTS, local, f"{TARGET}:{remote_path}"]
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired as exc:
        raise RemoteError(124, " ".join(argv), "timed out after 120s") from exc
    if result.returncode != 0:
        raise RemoteError(result.returncode, " ".join(argv), result.stderr)


def put_tree(local_dir, remote_dir):
    """Mirror a local directory onto the host with rsync (stale files deleted).

    Raises RemoteError on failure, or with returncode 124 if the sync stalls.
    """
    ssh = "ssh " + " ".join(SSH_OPTS)
    argv = ["rsync", "-a", "--delete", "-e", ssh,
            local_dir.rstrip("/") + "/", f"{TARGET}:{remote_dir.rstrip('/')}/"]
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired as exc:
        raise RemoteError(124, " ".join(argv), "timed out after 120s") from exc
    if result.returncode != 0:
        raise RemoteError(result.returncode, " ".join(argv), result.stderr)
