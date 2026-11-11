#!/usr/bin/env python3
"""Alert outside pane-tell when a caller or a detached queue cannot deliver.

Queued payloads are inspected only through file metadata. Caller failures keep
separate metadata containing the receiver and saved report path, never its text.
An alert acknowledges neither delivery nor permission to resend a payload.
"""
import fcntl
import glob
import hashlib
import importlib.util
import json
import math
import os
import stat
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.realpath(__file__))


def notify(env, title, body):
    child = {**os.environ, **env}
    override = child.get("HERDR_ERROR_NOTIFY")
    command = [override] if override else ["/usr/bin/python3", "-I", os.path.join(HERE, "herdr-error-notify.py")]
    try:
        if not override and child.get("PANE_TELL_ALERT_EVENT_ID"):
            event = {"id": child["PANE_TELL_ALERT_EVENT_ID"], "title": title, "body": body,
                     "created_at": child["PANE_TELL_ALERT_CREATED_AT"]}
            # Import keeps a stable ID even if this process dies before saving
            # its own deduplication state. workers-watch drains this queue.
            return subprocess.run([*command, "--import"], input=json.dumps([event]), text=True,
                                  env=child, timeout=15, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL).returncode == 0
        return subprocess.run([*command, title, body], env=child, timeout=15,
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def helper(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), os.path.join(HERE, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def enqueue_main(env, event_id, title, body):
    try:
        created = datetime.fromisoformat(env["PANE_TELL_ALERT_CREATED_AT"]).timestamp()
        helper("main-alert-inbox").enqueue(env, event_id, title, body, now=created)
        return True  # An identical event already stored is also durable.
    except (OSError, ValueError, KeyError, RuntimeError):
        return False


def wake_main(env):
    try:
        events = helper("main-alert-inbox").pending(env)
        return helper("main-alert-wake").tick(env, [item["event_id"] for item in events])
    except (OSError, ValueError, KeyError, RuntimeError):
        return False


def recover_batches(env):
    if not os.path.isdir(os.path.join(state_root(env), "batch")):
        return
    try:
        subprocess.run(["/bin/bash", os.path.join(HERE, "pane-tell"), "--recover-batch"],
                       env={**os.environ, **env}, timeout=5, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        pass  # Originals stay available to the next tick and metadata alert.


def seconds(env, name, default):
    try:
        value = float(env.get(name, default))
        return value if math.isfinite(value) and value >= 0 else default
    except (TypeError, ValueError):
        return default


def state_root(env):
    state = env.get("XDG_STATE_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".local/state")
    return os.path.join(state, "pane-tell")


def failed(env, receiver, report, now=None):
    """Persist before notifying, so a failed notifier is retried next launchd tick."""
    root = os.path.join(state_root(env), "failed-reports")
    try:
        os.makedirs(root, mode=0o700, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".new.", dir=root)
        try:
            with os.fdopen(descriptor, "w") as output:
                # This is a current failure, even if another tick initializes
                # its historical inventory before our own tick can run.
                json.dump({"receiver": receiver, "report": report, "main_alert_fresh": True}, output)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, os.path.join(root, "report." + os.path.basename(temporary)[5:]))
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    except OSError:
        # The original report was already saved by the caller. Even if this
        # metadata cannot be kept, attempt the independent error queue once.
        return notify(env, "세션 보고를 전달하지 못했습니다", f"전달 실패 수신처: {receiver}. 보관한 보고: {report}")
    tick(env, time.time() if now is None else now)


def tick(env, now):
    """Called by workers-watch before any herdr query, even when herdr is down."""
    try:
        recover_batches(env)
        return scan(env, now)
    finally:
        # A vanished source is not an acknowledgement of the main inbox.
        wake_main(env)


def save_metadata(path, value):
    raw = json.dumps(value)
    if len(raw.encode()) > 4 * 1024 * 1024:
        raise ValueError("relay metadata exceeds read limit")
    descriptor, temporary = tempfile.mkstemp(prefix=".watch-new.", dir=os.path.dirname(path))
    try:
        with os.fdopen(descriptor, "w") as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(os.path.dirname(path), os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_metadata(path):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    except FileNotFoundError:
        return {}
    with os.fdopen(descriptor) as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > 4 * 1024 * 1024:
            raise ValueError("unsafe relay alert outbox")
        value = json.load(source)
    return value


def pending_metadata(path):
    value = read_metadata(path)
    if not isinstance(value, dict) or any(
            not isinstance(key, str) or not isinstance(event, dict)
            or any(not isinstance(event.get(field), str) for field in ("title", "body", "created", "payload"))
            or any(not isinstance(event.get(field), bool) for field in ("main", "terminal"))
            or not valid_identity(event.get("identity"))
            or not isinstance(event.get("fresh", False), bool)
            for key, event in value.items()):
        raise ValueError("invalid relay alert outbox")
    return value


def event_identifier(payload, identity):
    digest = hashlib.sha256(json.dumps([payload, identity], sort_keys=True).encode()).digest()
    return str(uuid.UUID(bytes=digest[:16], version=4))


def file_identity(info):
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]


def valid_identity(value):
    return isinstance(value, list) and len(value) == 4 and all(type(n) is int and n >= 0 for n in value)


def baseline_metadata(path, inventory, previous, outbox, now):
    """Freeze an owned metadata-only inventory, including young queued files."""
    baseline = read_metadata(path)
    if not baseline:
        # An existing empty/corrupt manifest is not permission to reinitialize.
        if os.path.lexists(path):
            raise ValueError("empty relay alert baseline")
        entries = {}
        for collection in (previous, inventory):
            for payload, identity in collection.items():
                if isinstance(payload, str) and valid_identity(identity):
                    entries[event_identifier(payload, identity)] = {"payload": payload, "identity": identity}
        for event_id, event in outbox.items():
            if not event.get("fresh", False):
                entries.setdefault(event_id, {"payload": event["payload"], "identity": event.get("identity")})
        baseline = {"version": 1, "created_at": now, "summary_id": "relay-backlog-" + uuid.uuid4().hex,
                    "entries": entries}
        save_metadata(path, baseline)
    if not isinstance(baseline, dict) or set(baseline) != {"version", "created_at", "summary_id", "entries"} or \
            baseline["version"] != 1 or not isinstance(baseline["entries"], dict) or \
            not isinstance(baseline["created_at"], (int, float)) or not math.isfinite(baseline["created_at"]):
        raise ValueError("invalid relay alert baseline")
    inbox = helper("main-alert-inbox")
    inbox._id(baseline["summary_id"])
    for event_id, entry in baseline["entries"].items():
        inbox._id(event_id)
        if not isinstance(entry, dict) or set(entry) != {"payload", "identity"} or \
                not isinstance(entry["payload"], str) or not valid_identity(entry["identity"]):
            raise ValueError("invalid relay baseline identity")
    return baseline


def summarize_baseline(env, baseline, path):
    if not baseline["entries"]:
        return True  # Empty first scan is still permanently initialized.
    try:
        helper("main-alert-inbox").summarize(
            env, baseline["summary_id"], "기존 전달 보관 파일을 한 건으로 정리했습니다",
            f"처음 감시할 때 이미 있던 보관 기록 {len(baseline['entries'])}건입니다. "
            f"읽음이나 재전송 처리는 하지 않았습니다. 내용 없는 목록: {path}",
            sorted(baseline["entries"]), now=baseline["created_at"])
        return True
    except (OSError, ValueError, KeyError, RuntimeError):
        return False


def scan(env, now):
    root = state_root(env)
    if not os.path.isdir(root):
        return []
    limit = max(seconds(env, "PANE_TELL_ALERT", 600), seconds(env, "PANE_TELL_BATCH", 120) + 60)
    patterns = ("waiting/*", "undelivered/*", "batch/*/open/[0-9]*", "batch/*/sending.*/[0-9]*", "batch/*/claimed/[0-9]*",
                "batch/*/.joined.*", "batch/*/.new.*", "failed-reports/report.*")
    # Independent launchd ticks may overlap. Never hold up delivery on this lock.
    descriptor = os.open(os.path.join(root, ".watch.lock"), os.O_CREAT | os.O_RDWR | os.O_NONBLOCK | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a") as lock:
        info = os.fstat(lock.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            return []
        os.fchmod(lock.fileno(), 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return []
        candidates, inventory, fresh = {}, {}, set()
        for pattern in patterns:
            for payload in glob.glob(os.path.join(root, pattern)):
                try:
                    info = os.stat(payload, follow_symlinks=False)
                except FileNotFoundError:
                    continue  # delivery won the race
                if not stat.S_ISREG(info.st_mode) or not info.st_size:
                    continue
                identity = file_identity(info)
                failed_report = pattern == "failed-reports/report.*"
                if failed_report:
                    try:
                        failure = read_metadata(payload)
                        if isinstance(failure, dict) and failure.get("main_alert_fresh") is True:
                            fresh.add(payload)
                    except (OSError, ValueError):
                        pass
                if payload not in fresh:
                    inventory[payload] = identity
                if failed_report or now - info.st_mtime >= limit:
                    candidates[payload] = identity
        path = os.path.join(root, ".watch-alerts.json")
        try:
            previous = read_metadata(path)
            if not isinstance(previous, dict):
                previous = {}
        except (OSError, ValueError):
            previous = {}
        outbox_path = os.path.join(root, ".watch-pending.json")
        try:
            outbox = pending_metadata(outbox_path)
        except (OSError, ValueError):
            return []  # Preserve damaged metadata for inspection; never overwrite it.
        baseline_path = os.path.join(root, ".watch-baseline.json")
        try:
            baseline = baseline_metadata(baseline_path, inventory, previous, outbox, now)
        except ValueError:
            return []  # Refuse reinitialization of an unsafe or damaged manifest.
        baseline_applied = summarize_baseline(env, baseline, baseline_path)
        baseline_identities = {tuple(entry["identity"]) for entry in baseline["entries"].values()}
        accepted, alerts = {}, []
        for payload, identity in candidates.items():
            # It may have drained since scanning. Never read or modify its text.
            try:
                current = os.stat(payload, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if [current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns] != identity:
                continue
            title, body = "세션 사이의 글 전달이 지연됐습니다", f"전달 대기가 기준 시간을 넘었습니다. 보관 파일: {payload}"
            if os.path.dirname(payload) == os.path.join(root, "failed-reports"):
                title, body = "세션 보고를 전달하지 못했습니다", f"전달 실패 기록: {payload}"
                try:
                    failure = read_metadata(payload)
                    if isinstance(failure, dict) and all(isinstance(failure.get(key), str) for key in ("receiver", "report")):
                        body += f". 수신처: {failure['receiver']}. 보관한 보고: {failure['report']}"
                except (OSError, ValueError):
                    pass  # damaged failure metadata still needs an alert
            body = body[:700]  # Bound metadata before both transports.
            event_id = event_identifier(payload, identity)
            created = datetime.fromtimestamp(identity[3] / 1e9, timezone.utc).isoformat()
            terminal_done = previous.get(payload) == identity
            if terminal_done:
                accepted[payload] = identity
            outbox.setdefault(event_id, {"title": title, "body": body, "created": created,
                                       "payload": payload, "identity": identity,
                                       "main": False, "terminal": terminal_done, "fresh": payload in fresh})
        # Persist both transports before attempting either one. The source may
        # drain immediately afterwards, but failed imports still have a retry.
        save_metadata(outbox_path, outbox)
        for event_id, event in list(outbox.items()):
            alert_env = {**env, "PANE_TELL_ALERT_EVENT_ID": event_id,
                         "PANE_TELL_ALERT_CREATED_AT": event["created"]}
            historical = event_id in baseline["entries"] or tuple(event.get("identity", ())) in baseline_identities
            if historical:
                event["main"] = baseline_applied
            elif not event["main"]:
                event["main"] = enqueue_main(alert_env, event_id, event["title"], event["body"])
            if not event["terminal"]:
                event["terminal"] = notify(alert_env, event["title"], event["body"])
                if event["terminal"]:
                    alerts.append(event["payload"])
            if event["terminal"] and candidates.get(event["payload"]) == event.get("identity"):
                accepted[event["payload"]] = event["identity"]
            if event["main"] and event["terminal"]:
                del outbox[event_id]
            save_metadata(outbox_path, outbox)
        save_metadata(path, accepted)
        return alerts
