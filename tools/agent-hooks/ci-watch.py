#!/usr/bin/env python3
"""Watch one GitHub Actions run every 60 seconds, once per host/repo/run on this machine.

Exit 75 means another watcher owns this run, not that CI passed. The kernel drops
the lock when the watcher exits, including crashes; no stale PID cleanup is needed.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True, help='OWNER/REPO')
    parser.add_argument('run_id')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', args.repo) or not re.fullmatch(r'[1-9][0-9]*', args.run_id):
        parser.error('provide OWNER/REPO and a positive numeric run ID')
    host = os.environ.get('GH_HOST', 'github.com').lower()
    key = hashlib.sha256(f'{host}/{args.repo.lower()}/{args.run_id}'.encode()).hexdigest()
    root = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'ci-watch'
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (root / (key + '.lock')).open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f'ci-watch: {args.repo} run {args.run_id} is already watched; CI result unknown', file=sys.stderr)
            return 75
        try:
            # Let gh retain the lock even if this wrapper is killed while gh runs.
            result = subprocess.run(['gh', 'run', 'watch', args.run_id, '--repo', args.repo,
                                     '--interval', '60', '--exit-status'], pass_fds=(lock.fileno(),))
            return result.returncode if result.returncode >= 0 else 128 - result.returncode
        except OSError as error:
            print(f'ci-watch: cannot start gh ({type(error).__name__})', file=sys.stderr)
            return 127


if __name__ == '__main__':
    sys.exit(main())
