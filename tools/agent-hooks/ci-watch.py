#!/usr/bin/env python3
"""Watch one GitHub Actions run every 60 seconds, once per host/repo/run on this machine.

Exit 75 means another watcher owns this run, not that CI passed. The kernel drops
the lock when the watcher exits, including crashes; no stale PID cleanup is needed.
Use --status with the same repo/run to inspect the lock owner and last exit code.
An interrupted wrapper may leave a running gh; its unsaved result stays unknown.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time


def save_state(path: Path, state: dict):
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as temp:
        json.dump(state, temp)
        temporary = Path(temp.name)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def status(lock_path: Path, state_path: Path) -> dict:
    state = {}
    found = state_path.exists() or lock_path.exists()
    try:
        state = json.loads(state_path.read_text())
        if not isinstance(state, dict):
            state = {}
    except (OSError, ValueError):
        pass
    active = False
    try:
        with lock_path.open('r') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                active = True
    except FileNotFoundError:
        pass
    result = state.get('returncode')
    # A persisted result never outweighs a currently held lock.
    if active:
        result = None
    return {**state, 'active': active, 'returncode': result,
            'conclusion': 'running' if active else ('completed' if isinstance(result, int)
                          else ('unknown' if found else 'not_started')),
            'state_file': str(state_path)}


def watch_host_allowed() -> bool:
    config = Path.home() / '.config/munice'
    role = config / 'host-role'
    if role.exists() or role.is_symlink():
        return False
    try:
        target = (config / 'ambient-company-host').read_text().splitlines()[0].strip()
    except (OSError, IndexError):
        return False
    return target == socket.gethostname().split('.')[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True, help='OWNER/REPO')
    parser.add_argument('--status', action='store_true', help='inspect local ownership/result without calling gh')
    parser.add_argument('run_id')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', args.repo) or not re.fullmatch(r'[1-9][0-9]*', args.run_id):
        parser.error('provide OWNER/REPO and a positive numeric run ID')
    host = os.environ.get('GH_HOST', 'github.com').lower()
    key = hashlib.sha256(f'{host}/{args.repo.lower()}/{args.run_id}'.encode()).hexdigest()
    root = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'ci-watch'
    lock_path, state_path = root / (key + '.lock'), root / (key + '.json')
    if args.status:
        print(json.dumps(status(lock_path, state_path)))
        return 0
    if not watch_host_allowed():
        print('ci-watch: start run watchers on the configured session host; '
              'this host is read-only for watching (exit 78). Use --status for local history.', file=sys.stderr)
        return 78
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with lock_path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f'ci-watch: {args.repo} run {args.run_id} is already watched; CI result unknown. '
                  f'Inspect with --status; metadata: {state_path}', file=sys.stderr)
            return 75
        state = {'wrapper_pid': os.getpid(), 'gh_pid': None, 'started_at': time.time(),
                 'returncode': None}
        save_state(state_path, state)
        try:
            # Let gh retain the lock even if this wrapper is killed while gh runs.
            child = subprocess.Popen(['gh', 'run', 'watch', args.run_id, '--repo', args.repo,
                                      '--interval', '60', '--exit-status'], pass_fds=(lock.fileno(),))
        except OSError as error:
            print(f'ci-watch: cannot start gh ({type(error).__name__})', file=sys.stderr)
            code = 127
        else:
            state['gh_pid'] = child.pid
            save_state(state_path, state)
            code = child.wait()
            code = code if code >= 0 else 128 - code
        state.update(returncode=code, finished_at=time.time())
        save_state(state_path, state)
        return code


if __name__ == '__main__':
    sys.exit(main())
