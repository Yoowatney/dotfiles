#!/usr/bin/env python3
"""Queue terminal error alerts independently of herdr's routine toast setting.

Only a running, same-user, attached local herdr client may receive OSC 9. With
no suitable client, events remain pending. Remote callers export/import stable
event IDs and acknowledge the source only after the receiving queue accepted
them. A successful terminal write is not an acknowledgement from the human.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import tempfile
from typing import NamedTuple
import uuid


class Client(NamedTuple):
    pid: int
    uid: int
    tty: str
    started: str
    executable: str
    command: str


def sanitize(value, limit):
    """Remove terminal control characters, including OSC terminators."""
    if not isinstance(value, str):
        raise ValueError("notification text must be a string")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("notification text must be valid UTF-8") from exc
    return " ".join("".join(" " if ord(c) < 32 or 127 <= ord(c) <= 159 else c
                            for c in value).split())[:limit]


def valid_id(value):
    if not isinstance(value, str):
        raise ValueError("event ID must be a canonical UUID4")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ValueError("event ID must be a canonical UUID4") from exc
    if str(parsed) != value or parsed.version != 4:
        raise ValueError("event ID must be a canonical UUID4")
    return value


def validate_event(value):
    if not isinstance(value, dict) or set(value) != {"id", "title", "body", "created_at"}:
        raise ValueError("event must contain id, title, body and created_at")
    event_id = valid_id(value["id"])
    created_at = value["created_at"]
    if not isinstance(created_at, str) or len(created_at) > 64:
        raise ValueError("invalid event timestamp")
    try:
        timestamp = datetime.fromisoformat(created_at)
    except ValueError as exc:
        raise ValueError("invalid event timestamp") from exc
    if timestamp.tzinfo is None:
        raise ValueError("event timestamp must include timezone")
    title = sanitize(value["title"], 200)
    body = sanitize(value["body"], 1000)
    if not title or not body:
        raise ValueError("event title and body cannot be empty")
    return dict(id=event_id, title=title, body=body, created_at=created_at)


def eligible(client):
    if client.uid != os.getuid() or not re.fullmatch(r"ttys[0-9]+", client.tty):
        return False
    if Path(client.executable).name != "herdr":
        return False
    try:
        args = shlex.split(client.command)
    except ValueError:
        return False
    if not args or Path(args[0]).name != "herdr":
        return False
    # All other subcommands include servers, bridges and short-lived CLI calls.
    return len(args) == 1 or (len(args) in (3, 4) and args[1:3] == ["session", "attach"]
                              and (len(args) == 3 or not args[3].startswith("-")))


def parse_clients(output):
    clients = []
    for line in output.splitlines():
        fields = line.split(None, 9)
        if len(fields) != 10:
            continue
        try:
            client = Client(int(fields[0]), int(fields[1]), fields[2],
                            " ".join(fields[3:8]), fields[8], fields[9])
        except ValueError:
            continue
        if eligible(client):
            clients.append(client)
    return clients


def client_inventory():
    result = subprocess.run(["/bin/ps", "-axo", "pid=,uid=,tty=,lstart=,comm=,args="],
                            capture_output=True, text=True, check=True, timeout=5)
    return parse_clients(result.stdout)


def send_to_client(event, inventory):
    """Recheck the process identity immediately before writing a single TTY."""
    clients = [client for client in inventory() if eligible(client)]
    if not clients:
        raise OSError("no attached local herdr client")
    client = min(clients, key=lambda item: (len(shlex.split(item.command)) != 1, item.pid))
    path = "/dev/" + client.tty
    flags = os.O_WRONLY | os.O_NONBLOCK | os.O_NOCTTY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        device = os.fstat(descriptor)
        if not stat.S_ISCHR(device.st_mode) or device.st_uid != os.getuid():
            raise OSError("terminal is not a same-user character device")
        if client not in inventory():
            raise OSError("herdr client identity changed before delivery")
        current = os.stat(path, follow_symlinks=False)
        if (device.st_dev, device.st_ino, device.st_rdev) != (current.st_dev, current.st_ino, current.st_rdev):
            raise OSError("terminal identity changed before delivery")
        payload = ("\x1b]9;" + event["title"] + ": " + event["body"] + "\x1b\\").encode("utf-8")
        # One write keeps the frame together. A short write retains the event
        # for retry; an output failure must never mark an error as delivered.
        if os.write(descriptor, payload) != len(payload):
            raise OSError("short terminal write")
    finally:
        os.close(descriptor)


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / "state.json"

    @contextmanager
    def locked(self):
        descriptor = os.open(self.directory / "lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)

    def read(self):
        if not self.path.exists():
            return {"pending": [], "sent": []}
        state = json.loads(self.path.read_text())
        if not isinstance(state, dict) or set(state) != {"pending", "sent"}:
            raise ValueError("invalid queue state; refusing to overwrite")
        state["pending"] = [validate_event(item) for item in state["pending"]]
        state["sent"] = [valid_id(item) for item in state["sent"]]
        return state

    def save(self, state):
        descriptor, name = tempfile.mkstemp(prefix=".state-", dir=self.directory)
        try:
            with os.fdopen(descriptor, "w") as stream:
                json.dump(state, stream, ensure_ascii=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
            directory_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def log(self, message):
        descriptor = os.open(self.directory / "debug.log", os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
        with os.fdopen(descriptor, "a") as stream:
            stream.write(datetime.now(timezone.utc).isoformat() + " " + sanitize(message, 1000) + "\n")

    def enqueue(self, title, body):
        event = validate_event(dict(id=str(uuid.uuid4()), title=title, body=body,
                                    created_at=datetime.now(timezone.utc).isoformat()))
        self.import_events([event])
        return event

    def import_events(self, values):
        if not isinstance(values, list) or len(values) > 1000:
            raise ValueError("import must be an array of at most 1000 events")
        events = [validate_event(value) for value in values]
        with self.locked():
            state = self.read()
            pending = {event["id"]: event for event in state["pending"]}
            sent = set(state["sent"])
            added = 0
            for event in events:
                event_id = event["id"]
                if event_id in pending and pending[event_id] != event:
                    raise ValueError("event ID conflicts with an existing pending event")
                if event_id not in pending and event_id not in sent:
                    pending[event_id] = event
                    added += 1
            if added:
                state["pending"] = list(pending.values())
                self.save(state)
            return added

    def export(self):
        with self.locked():
            return self.read()["pending"]

    def ack(self, *event_ids):
        for event_id in event_ids:
            valid_id(event_id)
        acknowledged = set(event_ids)
        with self.locked():
            state = self.read()
            for event_id in event_ids:
                if event_id not in state["sent"]:
                    state["sent"].append(event_id)
            state["pending"] = [item for item in state["pending"] if item["id"] not in acknowledged]
            self.save(state)

    def drain(self, inventory=None):
        inventory = client_inventory if inventory is None else inventory
        delivered = 0
        with self.locked():
            state = self.read()
            for event in list(state["pending"]):
                try:
                    send_to_client(event, inventory)
                except (OSError, subprocess.SubprocessError) as exc:
                    self.log("pending " + event["id"] + ": " + str(exc))
                    break
                state["pending"].remove(event)
                state["sent"].append(event["id"])
                self.save(state)
                self.log("written " + event["id"])
                delivered += 1
        return delivered


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--drain", action="store_true")
    actions.add_argument("--export", action="store_true")
    actions.add_argument("--ack", metavar="ID", nargs="+")
    actions.add_argument("--import", dest="import_events", action="store_true")
    parser.add_argument("title", nargs="?")
    parser.add_argument("body", nargs="?")
    args = parser.parse_args()
    action = args.drain or args.export or args.ack or args.import_events
    if (action and (args.title is not None or args.body is not None)) or (not action and (args.title is None or args.body is None)):
        parser.error("provide title and body, or exactly one action")
    base = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    store = Store(base / "herdr-error-notify")
    if args.export:
        print(json.dumps(store.export(), ensure_ascii=False))
    elif args.ack:
        store.ack(*args.ack)
        print(json.dumps({"acknowledged": len(set(args.ack))}))
    elif args.import_events:
        data = sys.stdin.read(2_000_001)
        if len(data) > 2_000_000:
            raise ValueError("import exceeds 2 MB")
        print(json.dumps({"imported": store.import_events(json.loads(data))}))
    elif args.drain:
        print(json.dumps({"delivered": store.drain()}))
    else:
        event = store.enqueue(args.title, args.body)
        delivered = store.drain()
        print(json.dumps({"id": event["id"], "delivered": delivered}))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print("herdr-error-notify: " + str(exc), file=sys.stderr)
        sys.exit(1)
