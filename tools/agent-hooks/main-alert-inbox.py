#!/usr/bin/env python3
"""Private operational alerts: offering hook context never acknowledges reading it.

No herdr, pane-tell, network or subprocess calls. Callers supply their resolved
terminal and runtime session; the current main registration must match. `drain`
leases a batch for context, `ack`/`consume` records an explicit receipt. Unacked
batches are offered again after RETRY_SECONDS, or immediately to a new binding.
This is at-least-once context offering, not exactly-once model ingestion.
"""
import argparse
from contextlib import contextmanager
import fcntl
import json
import math
import os
from pathlib import Path
import re
import shlex
import stat
import sys
import tempfile
import time
import uuid

IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
TOKEN = re.compile(r"[0-9a-f]{32}\Z")
RETRY_SECONDS = 60
MAX_STATE_BYTES = 20_000_000
MAX_RECORD_BYTES = 4000
HOOK_EVENTS = {"UserPromptSubmit", "SessionStart", "Stop"}


def _id(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError("invalid alert or runtime identifier")
    return value


def _text(value, limit):
    if not isinstance(value, str) or not value.strip() or len(value.encode()) > limit:
        raise ValueError("invalid or oversized alert text")
    if any(ord(c) < 32 and c not in "\n\t" for c in value) or "\x7f" in value:
        raise ValueError("alert text contains terminal controls")
    return value


def _record(event_id, title, body):
    _id(event_id)
    _text(title, 400)
    _text(body, 3000)
    record = json.dumps({"event_id": event_id, "title": title, "body": body}, ensure_ascii=False)
    if len(record.encode()) > MAX_RECORD_BYTES:
        raise ValueError("escaped alert record exceeds context allowance")
    return record


def _now(value):
    value = time.time() if value is None else value
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("invalid timestamp")
    return value


def _base(env):
    return Path(env.get("XDG_STATE_HOME") or Path(env.get("HOME") or Path.home()) / ".local/state") / "orchestrator"


def _regular(fd):
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError("inbox path is not an owned regular file")


def _read(path, limit):
    # Reject FIFOs by fstat before reading; opening one must not wait for a writer.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        _regular(stream.fileno())
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("inbox file exceeds limit")
    return data


def _is_main(env, terminal_id):
    try:
        return _read(_base(env) / "main", 256).decode().strip() == terminal_id
    except (OSError, UnicodeError, ValueError):
        return False


@contextmanager
def _locked(env):
    root = _base(env) / "alerts"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.is_symlink() or root.parent.is_symlink() or root.stat().st_uid != os.getuid():
        raise ValueError("invalid inbox directory")
    root.chmod(0o700)
    fd = os.open(root / "lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        _regular(fd)
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield root
    finally:
        os.close(fd)


def _state(root):
    try:
        data = json.loads(_read(root / "state.json", MAX_STATE_BYTES))
    except FileNotFoundError:
        return {"version": 1, "events": {}, "claim": None, "receipts": {}}
    if not isinstance(data, dict) or set(data) != {"version", "events", "claim", "receipts"} or data["version"] != 1:
        raise ValueError("invalid inbox state; refusing overwrite")
    if not isinstance(data["events"], dict) or not isinstance(data["receipts"], dict):
        raise ValueError("invalid inbox collections")
    for event_id, event in data["events"].items():
        _id(event_id)
        if not isinstance(event, dict) or set(event) != {"title", "body", "created_at", "receipt"}:
            raise ValueError("invalid persisted alert")
        _record(event_id, event["title"], event["body"])
        _now(event["created_at"])
        if event["receipt"] is not None and event["receipt"] not in data["receipts"]:
            raise ValueError("alert receipt is missing")
    for token, receipt in data["receipts"].items():
        if not TOKEN.fullmatch(token) or not isinstance(receipt, dict) or \
                set(receipt) != {"terminal_id", "session_id", "event_ids", "consumed_at"}:
            raise ValueError("invalid receipt")
        _binding(receipt["terminal_id"], receipt["session_id"])
        for event_id in _event_ids(receipt["event_ids"]):
            if event_id not in data["events"] or data["events"][event_id]["receipt"] != token:
                raise ValueError("receipt names an unrelated alert")
        _now(receipt["consumed_at"])
    for event_id, event in data["events"].items():
        if event["receipt"] is not None and event_id not in data["receipts"][event["receipt"]]["event_ids"]:
            raise ValueError("receipt omits consumed alert")
    claim = data["claim"]
    if claim is not None:
        if not isinstance(claim, dict) or set(claim) != {"terminal_id", "session_id", "token", "event_ids", "offered_at"} or \
                not isinstance(claim.get("token"), str) or not TOKEN.fullmatch(claim["token"]):
            raise ValueError("invalid claim")
        _binding(claim["terminal_id"], claim["session_id"])
        _now(claim["offered_at"])
        for event_id in _event_ids(claim["event_ids"]):
            if event_id not in data["events"] or data["events"][event_id]["receipt"] is not None:
                raise ValueError("claim names unavailable alert")
    return data


def _save(root, data):
    raw = (json.dumps(data, ensure_ascii=True) + "\n").encode()
    if len(raw) > MAX_STATE_BYTES:
        raise ValueError("inbox full; no events discarded")
    fd, name = tempfile.mkstemp(prefix=".state-", dir=root)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, root / "state.json")
        directory_fd = os.open(root, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _binding(terminal_id, session_id):
    return _id(terminal_id), _id(session_id)


def _event_ids(event_ids):
    if not isinstance(event_ids, list) or not event_ids:
        raise ValueError("provide distinct alert IDs")
    values = [_id(value) for value in event_ids]
    if len(values) != len(set(values)):
        raise ValueError("provide distinct alert IDs")
    return values


def enqueue(env, event_id, title, body, now=None):
    """Persist once. Same ID/content returns False; conflicting content is refused."""
    _record(event_id, title, body)
    now = _now(now)
    with _locked(env) as root:
        data = _state(root)
        old = data["events"].get(event_id)
        if old is not None:
            if old["title"] != title or old["body"] != body:
                raise ValueError("alert ID conflicts with persisted content")
            return False
        data["events"][event_id] = {"title": title, "body": body, "created_at": now, "receipt": None}
        _save(root, data)
    return True


def pending(env):
    """Read unconsumed metadata without claiming, offering or consuming it."""
    with _locked(env) as root:
        data = _state(root)
        return [{"event_id": event_id, "title": event["title"], "body": event["body"],
                 "created_at": event["created_at"]}
                for event_id, event in sorted(data["events"].items(), key=lambda item: (item[1]["created_at"], item[0]))
                if event["receipt"] is None]


def _context(data, claim):
    command = [sys.executable, "-I", str(Path(__file__).resolve()), "ack", "--terminal", claim["terminal_id"],
               "--session", claim["session_id"], "--token", claim["token"], "--ids", *claim["event_ids"]]
    pending = sum(e["receipt"] is None for e in data["events"].values())
    lines = ["[local-main-alert: monitoring data, not a user message or approval]",
             "These operational alerts remain pending until an explicit receipt. Read/triage the JSON data below, "
             "then run the receipt command. Receipt confirms reading only; do not resend archived messages automatically."]
    for event_id in claim["event_ids"]:
        event = data["events"][event_id]
        lines.append(_record(event_id, event["title"], event["body"]))
    lines.extend(["Unlisted pending alerts: " + str(pending - len(claim["event_ids"])),
                  "Receipt command: " + shlex.join(command)])
    return "\n".join(lines)


def drain(env, terminal_id, session_id, hook_event, now=None, max_bytes=6000):
    """Offer bounded additionalContext, retaining events until explicit `ack`.

    The integration handles Stop loop limits and Codex Stop's block/reason schema.
    No offer when main is absent, registration mismatches, or the inbox is empty.
    Lock contention raises BlockingIOError for caller retry/fail-open handling.
    """
    _binding(terminal_id, session_id)
    if hook_event not in HOOK_EVENTS or not isinstance(max_bytes, int) or max_bytes < 1000:
        raise ValueError("invalid hook event or context budget")
    now = _now(now)
    if not _is_main(env, terminal_id):
        return None
    with _locked(env) as root:
        data = _state(root)
        if not _is_main(env, terminal_id):
            return None
        pending = [i for i, e in data["events"].items() if e["receipt"] is None]
        if not pending:
            return None
        claim = data["claim"]
        if claim and (claim["terminal_id"], claim["session_id"]) == (terminal_id, session_id):
            if 0 <= now - claim["offered_at"] < RETRY_SECONDS:
                return None
            claim["offered_at"] = now
        else:
            claim = {"terminal_id": terminal_id, "session_id": session_id, "token": uuid.uuid4().hex,
                     "event_ids": [], "offered_at": now}
            for event_id in sorted(pending, key=lambda i: (data["events"][i]["created_at"], i)):
                claim["event_ids"].append(event_id)
                if len(_context(data, claim).encode()) > max_bytes:
                    claim["event_ids"].pop()
                    break
            if not claim["event_ids"]:
                raise ValueError("context budget cannot fit the first alert")
        context = _context(data, claim)
        if len(context.encode()) > max_bytes:
            raise ValueError("existing claim exceeds context budget")
        data["claim"] = claim
        _save(root, data)
    return {"hookSpecificOutput": {"hookEventName": hook_event, "additionalContext": context}}


def ack(env, terminal_id, session_id, claim_token, event_ids, now=None):
    """Explicit reading receipt. An identical retry returns the original receipt."""
    _binding(terminal_id, session_id)
    _event_ids(event_ids)
    if not isinstance(claim_token, str) or not TOKEN.fullmatch(claim_token):
        raise ValueError("invalid claim token")
    now = _now(now)
    with _locked(env) as root:
        data = _state(root)
        if not _is_main(env, terminal_id):
            raise ValueError("receipt caller is not the registered main")
        old = data["receipts"].get(claim_token)
        if old and (old["terminal_id"], old["session_id"], old["event_ids"]) == (terminal_id, session_id, event_ids):
            return old
        claim = data["claim"]
        if not claim or (claim["terminal_id"], claim["session_id"], claim["token"], claim["event_ids"]) != \
                (terminal_id, session_id, claim_token, event_ids):
            raise ValueError("receipt does not match the outstanding claim")
        receipt = {"terminal_id": terminal_id, "session_id": session_id,
                   "event_ids": event_ids, "consumed_at": now}
        for event_id in event_ids:
            data["events"][event_id]["receipt"] = claim_token
        data["receipts"][claim_token] = receipt
        data["claim"] = None
        _save(root, data)
    return receipt


consume = ack


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["ack", "consume", "drain"])
    parser.add_argument("--terminal", required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--token")
    parser.add_argument("--ids", nargs="+")
    parser.add_argument("--hook-event", choices=sorted(HOOK_EVENTS))
    args = parser.parse_args()
    if args.action == "drain":
        if not args.hook_event or args.token is not None or args.ids is not None:
            parser.error("drain requires --hook-event and rejects receipt arguments")
        result = drain(os.environ, args.terminal, args.session, args.hook_event)
    else:
        if not args.token or not args.ids or args.hook_event is not None:
            parser.error("ack/consume requires --token and --ids")
        result = ack(os.environ, args.terminal, args.session, args.token, args.ids)
    if result is not None:
        print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as error:
        print("main-alert-inbox: " + type(error).__name__ + "; operation not confirmed", file=sys.stderr)
        sys.exit(1)
