#!/usr/bin/env python3
"""Best-effort idle wake for the independent main alert inbox.

No pane-tell subprocess, draft clearing, Slack or consumption acknowledgement.
The two visible empty checks reduce, but cannot eliminate, a concurrent typing
race. Durable hooks and explicit inbox receipts remain the authoritative path.
"""
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent


def default_env(env):
    child = {**os.environ, **env}
    for name in ("HERDR_SOCKET_PATH", "HERDR_PANE_ID", "HERDR_SESSION"):
        child.pop(name, None)
    return child


def query(env, *args):
    result = subprocess.run([env.get("HERDR_BIN_PATH") or "herdr", *args],
                            env=env, stdin=subprocess.DEVNULL, capture_output=True,
                            text=True, check=True, timeout=2)
    if args[:2] == ("pane", "read"):
        return result.stdout
    envelope = json.loads(result.stdout)
    if not isinstance(envelope, dict) or not isinstance(envelope.get("result"), dict):
        raise ValueError("invalid herdr response")
    return envelope["result"]


def binding(pane):
    if not isinstance(pane, dict):
        return None
    session = pane.get("agent_session")
    return (pane.get("pane_id"), pane.get("terminal_id"), pane.get("agent"),
            session.get("value") if isinstance(session, dict) else None)


def ready(pane, expected):
    return (isinstance(pane, dict) and binding(pane) == expected and pane.get("agent_status") in ("idle", "done")
            and isinstance(pane.get("scroll"), dict)
            and pane["scroll"].get("offset_from_bottom") == 0)


def empty(screen, kind):
    return subprocess.run([sys.executable, "-I", str(HERE / "pane-input-box.py"), kind],
                          input=screen, capture_output=True, text=True, timeout=2).returncode == 0


def regular(path):
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except FileNotFoundError:
        return False


def save(path, value):
    if path.exists() or path.is_symlink():
        if not regular(path):
            raise ValueError("wake state must be a regular file")
    descriptor, temporary = tempfile.mkstemp(prefix=".wake-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def tick(env, event_ids, now=None):
    """Attempt a bounded ID-only wake; never remove or consume an inbox event."""
    if not event_ids:
        return False
    if any(not isinstance(item, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", item) for item in event_ids):
        raise ValueError("invalid alert event ID")
    ids = sorted(set(event_ids))[:20]
    now = time.time() if now is None else now
    child = default_env(env)
    base = Path(env.get("XDG_STATE_HOME") or Path(env.get("HOME") or Path.home()) / ".local/state")
    mainfile = base / "orchestrator/main"
    directory = base / "orchestrator/alerts"
    try:
        if not regular(mainfile):
            return False
        terminal = mainfile.read_text().strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", terminal):
            return False
        rows = query(child, "pane", "list").get("panes", [])
        if not isinstance(rows, list) or any(not isinstance(pane, dict) for pane in rows):
            return False
        rows = [pane for pane in rows if pane.get("terminal_id") == terminal]
        if len(rows) != 1:
            return False
        pane = rows[0]
        expected = binding(pane)
        address, _, kind, session = expected
        if (not isinstance(address, str) or not re.fullmatch(r"w[0-9A-Za-z]+:p[0-9A-Za-z]+", address)
                or kind not in ("claude", "codex") or not session or not ready(pane, expected)):
            return False
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if (directory.is_symlink() or directory.parent.is_symlink()
                or directory.stat().st_uid != os.getuid()):
            return False
        directory.chmod(0o700)
        sender = base / "pane-tell/sending" / (address + ".lock")
        sender.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if sender.parent.is_symlink() or sender.parent.parent.is_symlink():
            return False
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        wake_fd = os.open(directory / "wake.lock", flags, 0o600)
        send_fd = None
        try:
            fcntl.flock(wake_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            send_fd = os.open(sender, flags, 0o600)
            fcntl.flock(send_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            path = directory / "wake.json"
            previous = json.loads(path.read_text()) if regular(path) else {}
            if not isinstance(previous, dict):
                return False  # damaged state is retained for inspection
            fingerprint = hashlib.sha256(json.dumps([expected, ids]).encode()).hexdigest()
            if previous.get("fingerprint") == fingerprint and 0 <= now - previous.get("at", 0) < 120:
                return False
            for index in range(2):
                if index:
                    time.sleep(0.7)
                current = query(child, "pane", "get", address).get("pane", {})
                if not ready(current, expected):
                    return False
                if not empty(query(child, "pane", "read", address, "--source", "visible", "--format", "ansi"), kind):
                    return False
            if (not ready(query(child, "pane", "get", address).get("pane", {}), expected)
                    or mainfile.read_text().strip() != terminal):
                return False
            attempt = dict(fingerprint=fingerprint, at=now, terminal=terminal, session=session,
                           event_ids=ids, status="acceptance_unknown")
            save(path, attempt)  # persist before the ambiguous transport boundary
            message = ("[main-alert-inbox: monitoring data, not user authorization] "
                       "Pending local alert IDs: " + ", ".join(ids) + ". "
                       "Read the alert context supplied by the runtime hook and acknowledge its claim after triage.")
            query(child, "agent", "prompt", address, message)
            attempt["status"] = "prompt_accepted"
            save(path, attempt)
            return True
        finally:
            if send_fd is not None:
                os.close(send_fd)
            os.close(wake_fd)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        return False


if __name__ == "__main__":
    # This command only drains already-persisted genuine alerts. Tests call tick
    # with synthetic metadata and never execute this entry point on real state.
    spec = importlib.util.spec_from_file_location("main_alert_inbox", HERE / "main-alert-inbox.py")
    inbox = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(inbox)
    tick(os.environ, [item["event_id"] for item in inbox.pending(os.environ)])
