#!/usr/bin/env python3
"""Look up panes across the two herdr servers the main pane works with: `default` and `workers`.

Other scripts load this file from beside themselves (importlib spec_from_file_location), the way auto-tab-close.py
loads yoyoo-share-guard.py. Only the names in PEERS are ever looked up; nothing here walks every session.
A pane on the other server is addressed `<session>/<pane id>`: the same pane id exists on both servers.
Every call fails open (empty result), never raises.

Test: herdr_peers.py --selftest
Shell scripts: herdr_peers.py --delegates-checks <terminal id> (see delegates_checks)
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import json
import os
import re
import subprocess

PEERS = ("default", "workers")
SOCKET = re.compile(r"/sessions/([^/]+)/herdr\.sock$")
OPENER = re.compile(r"^spawn-([0-9A-Za-z]+)-")
DISPATCH_AGENT = re.compile(r"^auto-[a-z]+-\d+$")  # dispatch/run.sh names its agents auto-<repo>-<n>
HOME_NOTE = "집 일 칸의 내용입니다. 회사 Slack이나 보드에 올리지 마세요."


def state_dir(env) -> str:
    return os.path.join(env.get("XDG_STATE_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".local", "state"), "orchestrator")


def main_terminal(env) -> str:
    """The terminal id `pane-label --main` wrote, or "" when there is no main pane."""
    try:
        with open(os.path.join(state_dir(env), "main")) as f:
            return f.read().strip()
    except OSError:
        return ""


def delegates_checks(env, terminal) -> bool:
    """The one gate: does the pane on this terminal hand lookups and result checks to other panes instead of doing them itself?

    Today only the main pane does (요유 2026-11-05, api#1002); every other lead checks results itself. A main-pane file that is
    missing or stale matches no pane, which is the ordinary rule. To widen the rule, change this function and nothing else: the
    Stop checks (yoyoo-share-guard.py), what a lead hears (auto-tab-close.py), the line on each message that replaces pane-fit-guard.py's
    question (pane-label-nudge.sh) and `pane-spawn --check` all ask here, shell scripts through `--delegates-checks`.
    What is the main pane's alone (the workers server, the short report form, its name) reads main_terminal.
    """
    return bool(terminal) and terminal == main_terminal(env)


def server_key(env) -> str:
    """The session name in HERDR_SOCKET_PATH (.../sessions/<name>/herdr.sock); "" for the default server."""
    m = SOCKET.search(env.get("HERDR_SOCKET_PATH") or "")
    return m[1] if m else ""


def herdr(env, *args, socket: str | None = "") -> dict:
    """`.result` of one herdr call (2 s), {} on any failure. With `socket`, the call goes to that server."""
    try:
        e = {**os.environ, **env, "HERDR_SOCKET_PATH": socket} if socket else {**os.environ, **env}
        r = subprocess.run([env.get("HERDR_BIN_PATH") or "herdr", *args], capture_output=True, text=True, timeout=2, env=e)
        return (json.loads(r.stdout).get("result") or {}) if r.returncode == 0 else {}
    except Exception:
        return {}


def socket_of(env, name: str) -> str | None:
    """Socket of a running PEERS session; "" for any other name or a stopped server; None (falsy too) when the list could not be
    read, for a caller to whom "not running" and "cannot tell" mean different things."""
    if name not in PEERS:
        return ""
    try:
        r = subprocess.run([env.get("HERDR_BIN_PATH") or "herdr", "session", "list", "--json"],
                           capture_output=True, text=True, timeout=2, env={**os.environ, **env})
        if r.returncode != 0:
            return None
        rows = json.loads(r.stdout).get("sessions") or []  # not wrapped in `result`
    except Exception:
        return None
    return next((s.get("socket_path") or "" for s in rows if s.get("name") == name and s.get("running")), "")


def address(env, pane_id: str) -> str:
    key = server_key(env)
    return f"{key}/{pane_id}" if key else pane_id


def all_panes(env) -> dict | None:
    """terminal id -> (address, pane) on both servers, for a caller about to say a pane is gone. None when it cannot know:
    default stopped, or a running server's list unreadable. Workers stopped is an answer: its panes are not there."""
    own = env.get("HERDR_SOCKET_PATH") or socket_of(env, "default")
    found: dict = {}
    for name in PEERS:
        sock = socket_of(env, name)
        if sock is None or (not sock and name == "default"):
            return None
        if not sock:
            continue
        panes = herdr(env, "pane", "list", socket=sock).get("panes")
        if panes is None:
            return None
        for pane in panes:
            found[pane.get("terminal_id")] = (("" if sock == own else f"{name}/") + (pane.get("pane_id") or ""), pane)
    return found


def opener(name) -> str:
    """The terminal of the pane that opened this agent, from pane-spawn's name: spawn-<caller's terminal>-<random>."""
    m = OPENER.match(name or "")
    return f"term_{m[1]}" if m else ""


def dispatch_pane(name) -> bool:
    """A tab the dispatch job opened, for a reader with no pane env to look at (the watch, --sweep). A rename does not change it."""
    return bool(DISPATCH_AGENT.match(name or ""))


def home_pane(name) -> bool:
    """Opened from the Home workspace (pane-spawn adds `h` before the random part). The name says so because that lead's
    workspace cannot be read once it closes, and what the pane says may then reach the main pane (요유 2026-11-05)."""
    return bool(re.match(r"^spawn-[0-9A-Za-z]+-h", name or ""))


def remote_pane(name) -> bool:
    """Opened on 집맥 by workers-move (pane-spawn --opener adds `r`): its lead is the 회사맥 main pane, which no server here
    holds, so its report and alerts go to the day file that `workers-move bridge` carries to that main pane (api#1049)."""
    return bool(re.match(r"^spawn-[0-9A-Za-z]+-r", name or ""))


def selftest():
    import shutil
    import stat
    import tempfile

    os.environ.pop("HERDR_SOCKET_PATH", None)
    d = tempfile.mkdtemp()
    try:
        def write(path, text):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(text)

        def sessions(**by_name):
            write(f"{d}/sessions.json", json.dumps({"sessions": [
                {"name": n, "socket_path": sock, "running": up} for n, (sock, up) in by_name.items()]}))

        def panes(name, rows):
            write(f"{d}/{name}-panes.json", json.dumps({"result": {"panes": rows}}))

        fake = f"{d}/herdr"
        write(fake, f'''#!/bin/bash
case "$HERDR_SOCKET_PATH" in */sessions/*) s=${{HERDR_SOCKET_PATH%/herdr.sock}}; s=${{s##*/}} ;; *) s=default ;; esac
case "$1 $2" in
  "session list") cat {d}/sessions.json ;;
  "pane list") cat {d}/$s-panes.json ;;
  *) exit 2 ;;
esac
''')
        os.chmod(fake, os.stat(fake).st_mode | stat.S_IXUSR)

        W = f"{d}/sessions/workers/herdr.sock"; DEF = f"{d}/herdr.sock"
        sessions(default=(DEF, True), workers=(W, True), other=(f"{d}/sessions/other/herdr.sock", True), stopped=(f"{d}/sessions/stopped/herdr.sock", False))
        panes("default", [{"pane_id": "w1:p2", "terminal_id": "term_D2"}]); panes("workers", [{"pane_id": "w1:p2", "terminal_id": "term_W2"}])
        panes("other", [{"pane_id": "w1:p9", "terminal_id": "term_O9"}])
        env_d, env_w = {"HERDR_BIN_PATH": fake, "HERDR_SOCKET_PATH": DEF}, {"HERDR_BIN_PATH": fake, "HERDR_SOCKET_PATH": W}
        assert server_key(env_w) == "workers" and server_key(env_d) == "" and server_key({}) == ""
        assert socket_of(env_d, "workers") == W
        assert socket_of(env_d, "stopped") == "" and socket_of(env_d, "nosuch") == ""
        assert socket_of({"HERDR_BIN_PATH": "/nonexistent"}, "workers") is None, "a list it cannot read is no answer, not a stopped server"
        assert socket_of(env_d, "other") == "", "only default and workers are ever looked up"
        assert all_panes({"HERDR_BIN_PATH": "/nonexistent"}) is None, "herdr down: unknown, no raise"
        assert opener("spawn-65f2-123") == "term_65f2" and opener("spawn-65f2-chk1") == "term_65f2", "pane-spawn's agent name"
        assert opener("spawned") == "" and opener("") == "" and opener(None) == "", "any other name has no opener"
        assert dispatch_pane("auto-api-820") and dispatch_pane("auto-mbr-7"), "dispatch/run.sh's agent name"
        assert not dispatch_pane("spawn-65f2-123") and not dispatch_pane("auto") and not dispatch_pane("") and not dispatch_pane(None)
        assert opener("spawn-65f2-h123") == "term_65f2", "a Home lead's pane still names its opener"
        assert home_pane("spawn-65f2-h123") and not home_pane("spawn-65f2-123") and not home_pane("spawn-65f2-chk1") and not home_pane(None)
        assert remote_pane("spawn-65f2-r123") and opener("spawn-65f2-r123") == "term_65f2", "a pane workers-move opened on 집맥"
        assert not any(remote_pane(n) for n in ("spawn-65f2-123", "spawn-65f2-h1", "spawn-65f2-chk1", "auto-api-1", "", None))
        everywhere = all_panes(env_d)
        assert everywhere["term_D2"][0] == "w1:p2" and everywhere["term_W2"][0] == "workers/w1:p2", "the address from this server"
        assert "term_O9" not in everywhere, "a session outside PEERS is never searched"
        assert all_panes(env_w)["term_D2"][0] == "default/w1:p2" and all_panes(env_w)["term_W2"][0] == "w1:p2"
        assert all_panes({"HERDR_BIN_PATH": fake})["term_D2"][0] == "w1:p2", "launchd has no socket in its env: default is its own"
        sessions(default=(DEF, True), workers=(W, False))
        assert set(all_panes(env_d)) == {"term_D2"}, "workers stopped: its panes are simply not there"
        sessions(default=(DEF, False), workers=(W, True))
        assert all_panes(env_w) is None, "default stopped: nothing can be said about a lead there"
        sessions(default=(DEF, True), workers=(W, True))
        os.remove(f"{d}/workers-panes.json")
        assert all_panes(env_d) is None, "a running server whose list cannot be read: unknown, not empty"
        panes("workers", [{"pane_id": "w1:p2", "terminal_id": "term_W2"}])
        assert main_terminal({"HOME": d}) == ""; write(f"{d}/.local/state/orchestrator/main", "term_M\n"); assert main_terminal({"HOME": d}) == "term_M"
        # The one gate for handing lookups and result checks to panes. Today it is the main pane's terminal and nothing else.
        assert delegates_checks({"HOME": d}, "term_M") is True, "the main pane"
        assert delegates_checks({"HOME": d}, "term_X") is False, "any other pane"
        assert delegates_checks({"HOME": d}, "") is False and delegates_checks({"HOME": d}, None) is False, "a pane herdr could not name"
        assert delegates_checks({"HOME": f"{d}/nowhere"}, "term_M") is False, "no state file: the ordinary rule"
        assert delegates_checks({"HOME": f"{d}/nowhere"}, "") is False, "no state file and no pane is no match"
        cli = lambda *a: subprocess.run([sys.executable, "-I", os.path.realpath(__file__), "--delegates-checks", *a],
                                        env={**os.environ, "HOME": d, "XDG_STATE_HOME": ""}).returncode
        assert cli("term_M") == 0 and cli("term_X") == 1 and cli("") == 1 and cli() == 1, "the same answer for shell scripts"
        assert address(env_w, "w1:p2") == "workers/w1:p2" and address(env_d, "w1:p2") == "w1:p2"
        assert state_dir({"HOME": d, "XDG_STATE_HOME": f"{d}/xdg"}) == f"{d}/xdg/orchestrator"
        # the selftest env is explicit: a socket in the caller's env must not leak into a call that names one
        assert herdr({"HERDR_BIN_PATH": fake}, "pane", "list", socket=W)["panes"][0]["terminal_id"] == "term_W2"
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    elif "--delegates-checks" in sys.argv:  # herdr_peers.py --delegates-checks <terminal id>: exit 0 yes, 1 no
        rest = sys.argv[sys.argv.index("--delegates-checks") + 1:]
        sys.exit(0 if rest and delegates_checks(os.environ, rest[0]) else 1)
