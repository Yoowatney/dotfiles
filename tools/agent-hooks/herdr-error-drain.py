#!/usr/bin/env python3
"""Drain terminal errors; the laptop imports its configured remote queue first.

The remote-host file exists only on the laptop. Import persists before remote
acknowledgement; stable IDs make an interrupted acknowledgement safe to retry.
No credentials or warning contents are written to logs by this relay.
"""
import fcntl
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import uuid

GATEWAY = str(Path(__file__).with_name("herdr-error-notify.py"))
REMOTE_GATEWAY = 'exec /usr/bin/python3 -I "$HOME/.dotfiles/tools/agent-hooks/herdr-error-notify.py"'


def drain(host, run=subprocess.run):
    local = [sys.executable, "-I", GATEWAY]
    ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=1"]
    def call(cmd, data=None):
        result = run(cmd, input=data, capture_output=True, text=True, timeout=20)
        if result.returncode:
            raise ValueError("command failed")
        return result.stdout
    try:
        if host and not host.startswith("-") and not any(c.isspace() for c in host):
            rows = json.loads(call(ssh + [host, REMOTE_GATEWAY + " --export"]))
            if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
                raise ValueError("invalid remote queue")
            # Match the gateway's maximum import batch. Leave the remainder at
            # the source for subsequent minutes rather than wedging a backlog.
            rows = rows[:1000]
            if any(not isinstance(r.get("id"), str) for r in rows):
                raise ValueError("invalid remote identifier")
            ids = [str(uuid.UUID(r["id"])) for r in rows]
            if rows:
                call(local + ["--import"], json.dumps(rows, ensure_ascii=False))
                call(ssh + [host, REMOTE_GATEWAY + " --ack " + " ".join(shlex.quote(i) for i in ids)])
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        pass  # The source retains unacknowledged rows; the next minute retries.
    try:
        call(local + ["--drain"])
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass  # The durable local queue retains rows with no usable client.


def main():
    root = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "herdr-error-notify"
    root.mkdir(parents=True, exist_ok=True)
    with (root / "relay.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        try:
            host = (Path.home() / ".config/herdr/remote-host").read_text().splitlines()[0].strip()
        except (OSError, IndexError):
            host = ""
        drain(host)


if __name__ == "__main__":
    main()
