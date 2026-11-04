#!/usr/bin/env python3
"""Look up panes across the two herdr servers the main pane works with: `default` and `workers`.

Other scripts load this file from beside themselves (importlib spec_from_file_location), the way auto-tab-close.py
loads yoyoo-share-guard.py. Only the names in PEERS are ever looked up; nothing here walks every session.
A pane on the other server is addressed `<session>/<pane id>`: the same pane id exists on both servers.
Every call fails open (empty result), never raises.

Test: herdr_peers.py --selftest
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


def state_dir(env) -> str:
    return os.path.join(env.get("XDG_STATE_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".local", "state"), "orchestrator")


def main_terminal(env) -> str:
    """The terminal id `pane-label --main` wrote, or "" when there is no main pane."""
    try:
        with open(os.path.join(state_dir(env), "main")) as f:
            return f.read().strip()
    except OSError:
        return ""


def server_key(env) -> str:
    """The session name in HERDR_SOCKET_PATH (.../sessions/<name>/herdr.sock); "" for the default server."""
    m = SOCKET.search(env.get("HERDR_SOCKET_PATH") or "")
    return m[1] if m else ""


def herdr(env, *args, socket: str = "") -> dict:
    """`.result` of one herdr call (2 s), {} on any failure. With `socket`, the call goes to that server."""
    try:
        e = {**os.environ, **env, "HERDR_SOCKET_PATH": socket} if socket else {**os.environ, **env}
        r = subprocess.run([env.get("HERDR_BIN_PATH") or "herdr", *args], capture_output=True, text=True, timeout=2, env=e)
        return (json.loads(r.stdout).get("result") or {}) if r.returncode == 0 else {}
    except Exception:
        return {}


def socket_of(env, name: str) -> str:
    """Socket of a running PEERS session; "" for any other name or a stopped server."""
    if name not in PEERS:
        return ""
    try:
        r = subprocess.run([env.get("HERDR_BIN_PATH") or "herdr", "session", "list", "--json"],
                           capture_output=True, text=True, timeout=2, env={**os.environ, **env})
        rows = (json.loads(r.stdout).get("sessions") or []) if r.returncode == 0 else []  # not wrapped in `result`
    except Exception:
        return ""
    return next((s.get("socket_path") or "" for s in rows if s.get("name") == name and s.get("running")), "")


def address(env, pane_id: str) -> str:
    key = server_key(env)
    return f"{key}/{pane_id}" if key else pane_id


def find_terminal(env, terminal: str):
    """(address, pane) of a terminal id: this server first, then the other PEERS server. None when not found."""
    own = env.get("HERDR_SOCKET_PATH") or socket_of(env, "default")
    for name, sock in [("", own)] + [(n, socket_of(env, n)) for n in PEERS]:
        if name and (not sock or sock == own):
            continue
        for pane in herdr(env, "pane", "list", socket=sock).get("panes") or []:
            if pane.get("terminal_id") == terminal:
                return (f"{name}/{pane.get('pane_id')}" if name else pane.get("pane_id") or ""), pane
    return None


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
        assert socket_of(env_d, "other") == "", "only default and workers are ever looked up"
        assert find_terminal(env_d, "term_D2")[0] == "w1:p2", "own server first"
        assert find_terminal(env_d, "term_W2")[0] == "workers/w1:p2", "the same pane id on the other server keeps its session"
        assert find_terminal(env_w, "term_D2")[0] == "default/w1:p2"
        assert find_terminal(env_d, "term_O9") is None, "a session outside PEERS is never searched"
        assert find_terminal({"HERDR_BIN_PATH": "/nonexistent"}, "term_D2") is None, "herdr down: nothing, no raise"
        assert main_terminal({"HOME": d}) == ""; write(f"{d}/.local/state/orchestrator/main", "term_M\n"); assert main_terminal({"HOME": d}) == "term_M"
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
