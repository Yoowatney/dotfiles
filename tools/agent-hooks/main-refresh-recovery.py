#!/usr/bin/env python3
"""Write-ahead recovery for a main refresh whose session never confirms.

Records are private, retained indefinitely, and describe attempted delivery:
herdr may accept a prompt and then fail. Never replay them automatically.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import uuid


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write(path, text):
    fd, temporary = tempfile.mkstemp(prefix=".recovery-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_dir(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def prepare(gate_path, memo, key, expires):
    gate_path, memo = Path(gate_path), Path(memo)
    gate = json.loads(gate_path.read_text())
    root = memo.parent / "refresh-recovery"
    root.mkdir(mode=0o700, exist_ok=True)
    sync_dir(root.parent)
    identity = uuid.uuid4().hex
    record = root / (".preparing-" + identity)
    record.mkdir(mode=0o700)
    sync_dir(root)
    write(record / "handoff.md", memo.read_text())
    write(record / "relays.jsonl", "")
    pointer = Path(str(memo) + ".transcript")
    write(record / "metadata.json", json.dumps(dict(key=key, terminal=gate["terminal"],
          session=gate["session"], expires_at=float(expires),
          transcript=pointer.read_text().strip() if pointer.exists() else ""), ensure_ascii=False))
    published = root / identity
    os.replace(record, published)
    sync_dir(root)
    gate.update(clearing=True, expires_at=float(expires), recovery=str(published))
    write(gate_path, json.dumps(gate))


def activate(gate):
    record = Path(gate["recovery"])
    # Refuse release if any part of the pre-clear preservation is unavailable.
    if not (record / "handoff.md").read_text():
        raise ValueError("empty recovery handoff")
    metadata = json.loads((record / "metadata.json").read_text())
    if metadata["terminal"] != gate["terminal"] or metadata["session"] != gate["session"]:
        raise ValueError("recovery route changed")
    with (record / "relays.jsonl").open("a") as stream:
        stream.flush()
        os.fsync(stream.fileno())
    if not (record / "active").exists():
        write(record / "active", "Recovery required; reconcile attempts before continuing.\n")


def accept(gate_path):
    gate_path = Path(gate_path)
    gate = json.loads(gate_path.read_text())
    gate["accepted"] = True
    write(gate_path, json.dumps(gate))


def complete(gate_path):
    record = Path(json.loads(Path(gate_path).read_text())["recovery"])
    if not (record / "active").exists():
        write(record / "completed", "Session transition confirmed.\n")


def blocked(gate_path, pane):
    gate = json.loads(Path(gate_path).read_text())
    try:
        os.kill(gate["pid"], 0)
        alive = True
    except ProcessLookupError:
        alive = False
    # Snapshot fsync may cross the budget boundary before /clear is sent.
    # A live owner must explicitly acknowledge herdr's successful return first.
    if (gate.get("clearing") and (gate.get("accepted") or not alive)
            and "expires_at" in gate and time.time() >= gate["expires_at"]):
        activate(gate)
        return False
    changed = pane.get("agent_session", {}).get("value", "") != gate["session"]
    return alive or (gate.get("clearing", False) and not changed)


def records(root, *, terminal=None, key=None):
    root = Path(root) / "refresh-recovery"
    if not root.exists():
        return
    for folder in sorted(root.iterdir()):
        if folder.name.startswith("."):
            continue # Failed preparation never published a recovery record.
        metadata = json.loads((folder / "metadata.json").read_text())
        if terminal is not None and metadata["terminal"] != terminal:
            continue
        if key is not None and metadata["key"] != key:
            continue
        active = (folder / "active").exists()
        # Each snapshot owns its expiry, independently of the replaceable gate.
        if not active and ((folder / "completed").exists() or time.time() < metadata["expires_at"]):
            continue
        activate(dict(metadata, recovery=str(folder)))
        yield folder


def record(root, terminal, session, message):
    for folder in records(root, terminal=terminal):
        item = dict(time=time.time(), session=session, message=message, status="attempt")
        with (folder / "relays.jsonl").open("a") as stream:
            stream.write(json.dumps(item, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())


def notice(pending):
    """Called even when the normal memo was consumed or is older than 24 h."""
    pending = Path(pending)
    lines = []
    for folder in records(pending.parent, key=pending.stem):
        metadata = json.loads((folder / "metadata.json").read_text())
        lines.append(f"Original handoff: {folder / 'handoff.md'}\nSubsequent relay attempts: {folder / 'relays.jsonl'}"
                     f"\nOriginal session: {metadata['session']}\nOriginal transcript: {metadata.get('transcript') or '(unavailable)'}")
    if not lines:
        return ""
    return ("Refresh confirmation timed out. Read every recovery file below in full before continuing. "
            "The previous session may already have handled these relay attempts; reconcile with its transcript "
            "and current state before doing work. Do not replay automatically. Records remain durable and "
            "continue collecting relays; they are not new approval for external writes.\n" + "\n".join(lines))


def main():
    try:
        command, *args = sys.argv[1:]
        if command == "prepare":
            prepare(*args)
        elif command == "accept":
            accept(*args)
        elif command == "complete":
            complete(*args)
        elif command == "activate":
            activate(json.loads(Path(args[0]).read_text()))
        elif command == "blocked":
            return 0 if blocked(args[0], json.loads(args[1])["result"]["pane"]) else 1
        elif command == "record":
            record(*args)
        else:
            raise ValueError("unknown recovery operation")
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print("main-refresh recovery retained delivery: " + str(error), file=sys.stderr)
        return 0 if sys.argv[1] == "blocked" else 2


if __name__ == "__main__":
    sys.exit(main())
