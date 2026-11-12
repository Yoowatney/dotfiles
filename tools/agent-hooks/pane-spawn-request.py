#!/usr/bin/env python3
"""Serialize repeated worker requests without persisting their instruction text."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

WINDOW_SECONDS = 600


def inventory(socket):
    env = os.environ.copy()
    if socket:
        env["HERDR_SOCKET_PATH"] = socket
    else:
        env.pop("HERDR_SOCKET_PATH", None)
    result = subprocess.run(
        [env.get("HERDR_BIN_PATH", "herdr"), "pane", "list"],
        env=env, capture_output=True, text=True, timeout=10,
    )
    if result.returncode:
        raise RuntimeError("cannot verify existing panes; retry later or use --allow-duplicate intentionally")
    panes = json.loads(result.stdout)["result"]["panes"]
    if not isinstance(panes, list):
        raise ValueError("invalid pane inventory")
    return panes


def save(path, record):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record))
    temporary.replace(path)


def run():
    shell, launcher, caller, cwd, prompt, kind, auto_claude, *arguments = sys.argv[1:]
    socket = os.environ.get("HERDR_SOCKET_PATH", "")
    # Label, job id and startup-generated prompt text do not change the assignment.
    key = hashlib.sha256(json.dumps(
        [socket, caller, str(Path(cwd).resolve()), prompt], ensure_ascii=False,
    ).encode()).hexdigest()
    state = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    root = state / "pane-spawn/requests"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    record_path = root / f"{key}.json"
    receipt_path = root / f"{key}.receipt"
    with (root / f"{key}.lock").open("a") as lock:
        # Hold the lock across creation, startup and delivery. Concurrent first calls
        # must wait for the same result rather than both seeing an empty cache.
        fcntl.flock(lock, fcntl.LOCK_EX)
        now = time.time()
        if record_path.exists():
            try:
                record = json.loads(record_path.read_text())
                age = now - record["created_at"]
                if 0 <= age < WINDOW_SECONDS:
                    panes = inventory(record["socket"])
                    terminal = record.get("terminal_id")
                    if not terminal:
                        raise RuntimeError("previous pane identity is unknown; inspect it before an intentional retry")
                    match = next((p for p in panes if p.get("terminal_id") == terminal), None)
                    if match:
                        prefix = "workers/" if record["address"].startswith("workers/") else ""
                        address = prefix + match["pane_id"]
                        print(f"pane-spawn: reusing {address} (same instruction within ten minutes; --allow-duplicate starts another)", file=sys.stderr)
                        print(address)
                        return record["exit_code"]
            except (KeyError, TypeError, ValueError) as error:
                raise RuntimeError("request record is damaged; inspect existing work before using --allow-duplicate") from error
        # A previous process may have stopped after opening a pane but before saving
        # its final result. Never silently duplicate that unconfirmed work.
        if receipt_path.exists():
            raise RuntimeError("an interrupted request may have opened a pane; inspect existing work before using --allow-duplicate")
        env = os.environ.copy()
        env["PANE_SPAWN_REQUEST_PARENT"] = str(os.getpid())
        env["PANE_SPAWN_REQUEST_KIND"] = kind
        env["PANE_SPAWN_REQUEST_AUTO_CLAUDE"] = auto_claude
        env["PANE_SPAWN_REQUEST_RECEIPT"] = str(receipt_path)
        started_at = time.time()
        child = subprocess.Popen([shell, launcher, *arguments], env=env, stdout=subprocess.PIPE, text=True)
        def forward(signum, _frame):
            child.send_signal(signum)
        previous = {sig: signal.signal(sig, forward) for sig in (signal.SIGTERM, signal.SIGINT)}
        try:
            output, _ = child.communicate()
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
        # The receipt survives interrupted launches; no instruction text is stored.
        if receipt_path.exists():
            receipt = json.loads(receipt_path.read_text())
            record = {**receipt, "created_at": started_at, "exit_code": max(child.returncode, 1), "terminal_id": None}
            try:
                panes = inventory(receipt["socket"])
                pane_id = receipt["address"].removeprefix("workers/")
                match = next((p for p in panes if p.get("pane_id") == pane_id), None)
                if match:
                    record["terminal_id"] = match["terminal_id"]
                    record["exit_code"] = child.returncode if child.returncode >= 0 else 1
                    save(record_path, record)
                else:
                    record_path.unlink(missing_ok=True)
            except (RuntimeError, subprocess.TimeoutExpired, KeyError, TypeError, ValueError):
                save(record_path, record)
            receipt_path.unlink()
        print(output, end="")
        return child.returncode if child.returncode >= 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(run())
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        print(f"pane-spawn: duplicate protection: {error}", file=sys.stderr)
        sys.exit(1)
