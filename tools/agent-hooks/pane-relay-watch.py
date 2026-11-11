#!/usr/bin/env python3
"""Alert outside pane-tell when a caller or a detached queue cannot deliver.

Queued payloads are inspected only through file metadata. Caller failures keep
separate metadata containing the receiver and saved report path, never its text.
An alert acknowledges neither delivery nor permission to resend a payload.
"""
import fcntl
import glob
import json
import math
import os
import stat
import subprocess
import tempfile
import time

HERE = os.path.dirname(os.path.realpath(__file__))


def notify(env, title, body):
    child = {**os.environ, **env}
    override = child.get("HERDR_ERROR_NOTIFY")
    command = [override] if override else ["/usr/bin/python3", "-I", os.path.join(HERE, "herdr-error-notify.py")]
    try:
        return subprocess.run([*command, title, body], env=child, timeout=15,
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


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
                json.dump({"receiver": receiver, "report": report}, output)
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
    root = state_root(env)
    if not os.path.isdir(root):
        return []
    limit = max(seconds(env, "PANE_TELL_ALERT", 600), seconds(env, "PANE_TELL_BATCH", 120) + 60)
    patterns = ("waiting/*", "undelivered/*", "batch/*/open/[0-9]*", "batch/*/sending.*/[0-9]*",
                "batch/*/.joined.*", "batch/*/.new.*", "failed-reports/report.*")
    candidates = {}
    for pattern in patterns:
        for path in glob.glob(os.path.join(root, pattern)):
            try:
                info = os.stat(path, follow_symlinks=False)
            except FileNotFoundError:
                continue  # delivery won the race
            failed_report = pattern == "failed-reports/report.*"
            if stat.S_ISREG(info.st_mode) and info.st_size and (failed_report or now - info.st_mtime >= limit):
                candidates[path] = [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]
    # Independent launchd ticks may overlap. Never hold up delivery on this lock.
    with open(os.path.join(root, ".watch.lock"), "a") as lock:
        os.fchmod(lock.fileno(), 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return []
        path = os.path.join(root, ".watch-alerts.json")
        try:
            with open(path) as source:
                previous = json.load(source)
            if not isinstance(previous, dict):
                previous = {}
        except (OSError, ValueError):
            previous = {}
        accepted, alerts = {}, []
        for payload, identity in candidates.items():
            if previous.get(payload) == identity:
                accepted[payload] = identity
                continue
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
                    with open(payload) as source:
                        failure = json.load(source)
                    if isinstance(failure, dict) and all(isinstance(failure.get(key), str) for key in ("receiver", "report")):
                        body += f". 수신처: {failure['receiver']}. 보관한 보고: {failure['report']}"
                except (OSError, ValueError):
                    pass  # damaged failure metadata still needs an alert
            if notify(env, title, body):
                accepted[payload] = identity
                alerts.append(payload)
        descriptor, temporary = tempfile.mkstemp(prefix=".watch-alerts.", dir=root)
        try:
            with os.fdopen(descriptor, "w") as output:
                json.dump(accepted, output)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return alerts
