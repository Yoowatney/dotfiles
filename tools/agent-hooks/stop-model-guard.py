#!/usr/bin/env python3
"""Exit 0 to skip Stop model work, 1 to keep it. Never print process data.

The nearest CLI owns this Stop; only an *older* CLI in its process ancestry
can make it a worker's nested test session. Child probes may clear HERDR_*,
so read the ancestor's original environment instead of the hook's environment.
No session startup, Bash command, or the worker's own Stop is blocked here.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import struct
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def marker(env, session=''):
    session = session or env.get('CODEX_THREAD_ID') or env.get('CODEX_SESSION_ID') or env.get('CLAUDE_SESSION_ID') or ''
    if not isinstance(session, str) or not re.fullmatch(r'[a-zA-Z0-9_-]+', session):
        return None
    return Path(env.get('HOME', str(Path.home()))) / '.local/state/agent-model-free' / session


def process_data(kernel, pid):
    raw = kernel._sysctl([1, 49, pid])  # KERN_PROCARGS2, same reader as fork-watch
    argc, = struct.unpack_from('i', raw)
    parts = raw[4:].split(b'\0')
    index = 1  # executable path, NUL padding, argv, environment
    while index < len(parts) and not parts[index]:
        index += 1
    args = [p.decode(errors='replace') for p in parts[index:index + argc]]
    # Keep only routing fields; never expose credentials or prompt arguments.
    allowed = {'HOME', 'XDG_STATE_HOME', 'HERDR_PANE_ID', 'HERDR_SESSION',
               'HERDR_SOCKET_PATH', 'HERDR_BIN_PATH'}
    env = {}
    for part in parts[index + argc:]:
        key, sep, value = part.partition(b'=')
        name = key.decode(errors='replace')
        if sep and name in allowed:
            env[name] = value.decode(errors='replace')
    return args, env


def agent(args):
    if not args:
        return False
    name = Path(args[0]).name
    if name in {'codex', 'claude'}:
        return True
    # Native Claude's versioned executable and its older Node distribution.
    if re.search(r'/claude/versions/[^/]+$', args[0]):
        return True
    return name in {'node', 'nodejs'} and len(args) > 1 and '/claude-code/cli.js' in args[1]


def worker(peers, env):
    if not env.get('HERDR_PANE_ID'):
        return False
    pane = peers.herdr(env, 'pane', 'get', env['HERDR_PANE_ID']).get('pane') or {}
    if not pane.get('pane_id') or not pane.get('terminal_id') or peers.delegates_checks(env, pane['terminal_id']):
        return False
    info = peers.herdr(env, 'agent', 'get', pane['pane_id']).get('agent') or {}
    return bool(peers.opener(info.get('name')))


def nested_worker():
    kernel = load('stop_fork_watch', 'fork-watch.py')
    peers = kernel.herdr_peers
    parents = {p.pid: p.ppid for p in kernel.list_procs()}
    pid, seen, own_agent = os.getpid(), set(), False
    while pid > 1 and pid not in seen:
        seen.add(pid)
        try:
            args, env = process_data(kernel, pid)
        except (OSError, ValueError, struct.error):
            args, env = [], {}
        if agent(args):
            if own_agent and worker(peers, env):
                return True
            own_agent = True
        pid = parents.get(pid, 0)
    return False


def skip(session=''):
    path = marker(os.environ, session)
    return bool(path and path.is_file()) or nested_worker()


def remember(session):
    if not session:
        return  # Never mark an inherited parent ID when SessionStart JSON is missing.
    path = marker(os.environ, session)
    if not path or path.is_file() or not nested_worker():
        return
    # Reuse the opt-in marker store. SessionStart is synchronous, so an async
    # Stop stays protected even after its CLI or the parent worker has exited.
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return
    try:
        os.write(fd, b'nested-worker\n')
    finally:
        os.close(fd)


if __name__ == '__main__':
    try:
        session = ''
        if '--stdin' in sys.argv or '--remember' in sys.argv:
            try:
                payload = json.load(sys.stdin)
                session = (payload.get('session_id') or '') if isinstance(payload, dict) else ''
            except (ValueError, OSError):
                pass
        if '--remember' in sys.argv:
            remember(session)
            sys.exit(0)
        sys.exit(0 if skip(session) else 1)
    except Exception:
        # An unreadable/gone ancestor must not disable a normal work log.
        sys.exit(1)
