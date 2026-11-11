#!/usr/bin/env python3
"""Preserve a registered main pane's visible draft before a relay, never answer a dialog.

The archive keeps the rendered text AND its ANSI box: wrapping and terminal padding
are display information, not an exact reconstruction of the editor buffer. A
collapsed paste, attachment or clipped editor must wait rather than lose content.
Caller serializes prepare/prompt/ack with pane-tell's per-pane flock.
"""
import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time


class Unsafe(Exception):
    pass


def herdr(*args):
    return subprocess.run([os.environ.get("HERDR_BIN_PATH", "herdr"), *args],
                          capture_output=True, text=True, timeout=5, check=True).stdout


def root():
    return Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))


def pane_record(pane):
    return json.loads(herdr("pane", "get", pane))["result"]["pane"]


def pane_info(pane, kind=None):
    info = pane_record(pane)
    main = (root() / "orchestrator/main").read_text().strip()
    if (not main or info.get("terminal_id") != main or info.get("agent_status") not in {"idle", "done", "working"}
            or info.get("scroll", {}).get("offset_from_bottom") != 0
            or (kind is not None and info.get("agent") != kind)):
        raise Unsafe("not a visible, unblocked registered main editor")
    return info


ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
DIM = re.compile(r"\x1b\[2m.*?(?:\x1b\[0m|$)")


def draft(pane, kind):
    lines = herdr("pane", "read", pane, "--source", "visible", "--format", "ansi").replace("\r", "").split("\n")
    plain = lambda line: ANSI.sub("", line)
    if kind == "claude":
        rules = [i for i, line in enumerate(lines) if re.fullmatch(r"─{10,}( .* ─+)?", plain(line).strip())]
        if len(rules) < 2:
            raise Unsafe("editor boundary missing")
        box = lines[rules[-2] + 1:rules[-1]]
        if not box or not plain(box[0]).startswith("❯\xa0"):
            raise Unsafe("editor prompt missing")
    elif kind == "codex":
        # The editor prompt is at column zero. A continuation may itself start
        # with › after its two-column indent; never mistake it for the boundary.
        marks = [i for i, line in enumerate(lines) if plain(line).startswith("›")]
        if not marks or not plain(DIM.sub("", lines[marks[-1]])).startswith("›"):
            raise Unsafe("editor prompt missing")
        start = marks[-1]
        end = start + 1
        while end < len(lines) and "\x1b[48;" in lines[end]:
            end += 1
        box = lines[start:end]
        if len(box) < 2 or plain(box[-1]).strip():
            raise Unsafe("editor bottom missing")
    else:
        raise Unsafe("unknown editor")
    text = [plain(DIM.sub("", box[0]))[2:].rstrip()]
    text.extend(plain(line)[2:].rstrip() for line in box[1:])
    while text and not text[-1]:
        text.pop()
    rendered = "\n".join(text)
    if re.search(r"\[(?:Pasted|Image|image|Attached)|(?:↑|↓).*\d+.*(?:lines|行)|…", rendered):
        raise Unsafe("collapsed or clipped content")
    return {"rendered_text": rendered, "ansi_box": "\n".join(box), "rows": len(box)}


def pending_path(info):
    # Terminal ids come from herdr, but do not let a malformed response choose a path.
    terminal = info["terminal_id"]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", terminal):
        raise Unsafe("invalid terminal id")
    return root() / "pane-tell/drafts/pending" / terminal


def durable(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def save(info, content):
    directory = root() / "pane-tell/drafts"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.datetime.now().astimezone().isoformat()
    fd, name = tempfile.mkstemp(prefix=datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S_"), suffix=".json", dir=directory)
    os.close(fd)
    path = Path(name)
    durable(path, json.dumps({"saved_at": stamp, "pane_id": info["pane_id"],
                             "terminal_id": info["terminal_id"], "agent": info["agent"],
                             **content}, ensure_ascii=False, indent=2) + "\n")
    pending = pending_path(info)
    paths = pending.read_text().splitlines() if pending.exists() else []
    durable(pending, "\n".join([*paths, str(path.resolve())]) + "\n")


def prepare(pane, kind, check=False):
    info = pane_info(pane, kind)
    first = draft(pane, kind)
    time.sleep(float(os.environ.get("PANE_TELL_SETTLE", "0.7")))
    if pane_info(pane, kind)["terminal_id"] != info["terminal_id"] or draft(pane, kind) != first:
        raise Unsafe("editor changed between reads")
    if check:
        return True
    if first["rendered_text"]:
        save(info, first)  # Archive AND pending notice must be durable before any key.
        if pane_info(pane, kind)["terminal_id"] != info["terminal_id"] or draft(pane, kind) != first:
            raise Unsafe("editor changed while saving")
        # Delete after the cursor, then before it. Extra iterations on an empty
        # editor do nothing; no Enter, Escape, history navigation or confirmation.
        rows = first["rows"] + 1
        herdr("agent", "send-keys", pane, *(["ctrl+k"] * (2 * rows) + ["ctrl+u", "Backspace"] * rows))
        time.sleep(float(os.environ.get("PANE_TELL_SETTLE", "0.7")))
        if draft(pane, kind)["rendered_text"]:
            raise Unsafe("editor did not clear")
    pending = pending_path(info)
    paths = pending.read_text().splitlines() if pending.exists() else []
    return "메인 세션 입력창의 쓰다 만 글을 저장했습니다: " + ", ".join(paths) if paths else ""


def ack(pane):
    pending_path(pane_record(pane)).unlink(missing_ok=True)


def notice(pane):
    # A successful clear can be retried after status or main registration changes.
    # Its pending notice still belongs to this terminal and must not be dropped.
    pending = pending_path(pane_record(pane))
    paths = pending.read_text().splitlines() if pending.exists() else []
    return "메인 세션 입력창의 쓰다 만 글을 저장했습니다: " + ", ".join(paths) if paths else ""


if __name__ == "__main__":
    try:
        mode, pane, *rest = sys.argv[1:]
        if mode == "ack":
            ack(pane)
        elif mode == "notice":
            print(notice(pane))
        else:
            notice = prepare(pane, rest[0], check=mode == "check")
            if mode != "check":
                print(notice)
    except (Unsafe, OSError, ValueError, KeyError, subprocess.SubprocessError):
        sys.exit(1)
