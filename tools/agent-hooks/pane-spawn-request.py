#!/usr/bin/env python3
"""Track worker requests and issue assignments without persisting instructions."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

WINDOW_SECONDS = 600
ISSUE_URL = re.compile(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/issues/[0-9]+")


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


def record_assignment(address, session_id, name, prompt):
    urls = sorted(set(ISSUE_URL.findall(name + "\n" + prompt)))
    if not urls:
        return
    pane_id = address.rsplit("/", 1)[-1]
    # Work can start before the runtime watcher publishes its session identity.
    # Re-read only that identity; never resend an already delivered instruction.
    deadline = time.monotonic() + 5
    while not session_id:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("missing agent session identity after waiting")
        result = subprocess.run(
            [os.environ.get("HERDR_BIN_PATH", "herdr"), "agent", "get", pane_id],
            capture_output=True, text=True, timeout=remaining,
        )
        if result.returncode:
            raise RuntimeError("cannot read agent session identity")
        agent = json.loads(result.stdout).get("result", {}).get("agent", {})
        session_id = (agent.get("agent_session") or {}).get("value", "")
        if not session_id:
            time.sleep(min(0.25, max(0, deadline - time.monotonic())))
    socket = os.environ.get("HERDR_SOCKET_PATH", "")
    panes = inventory(socket)
    pane = next((p for p in panes if p.get("pane_id") == pane_id), None)
    if not pane or not pane.get("terminal_id"):
        raise RuntimeError("missing terminal identity")
    # Launcher output is caller-relative. Durable bindings use server-qualified
    # addresses even when a worker launches another pane on its own server.
    socket_path = Path(socket)
    server = socket_path.parent.name if socket_path.parent.parent.name == "sessions" else "default"
    address = f"{server}/{pane_id}" if server != "default" else pane_id
    root = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "pane-spawn"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    with (root / "assignments.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = root / "assignments.json"
        rows = json.loads(path.read_text()) if path.exists() else []
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError("invalid assignment inventory")
        rows = [row for row in rows if row.get("session_id") != address
                and row.get("terminal_id") != pane["terminal_id"]]
        rows.append(dict(session_id=address, terminal_id=pane["terminal_id"],
                         agent_session_id=session_id, issue_urls=urls))
        save(path, rows)
        path.chmod(0o600)


def run():
    if sys.argv[1:2] == ["--record-assignment"]:
        record_assignment(*sys.argv[2:])
        return 0
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
        operation = "assignment recording" if sys.argv[1:2] == ["--record-assignment"] else "duplicate protection"
        print(f"pane-spawn: {operation}: {error}", file=sys.stderr)
        sys.exit(1)
