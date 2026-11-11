#!/usr/bin/env python3
"""Test-only launcher: watch an owned live fixture, using one initial snapshot."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent


def capture(watch, env, pgid, mode):
    if pgid <= 1 or mode not in ('alive', 'orphan'):
        raise RuntimeError('invalid owned group identity')
    rows = watch.list_procs()
    now, main = time.time(), watch.Main()
    main_pid = main.get(env, {p.pid: p for p in rows}, now)
    keep = watch.keepers(rows, watch.argv_of, main_pid, os.getpid())
    owned = {(p.pid, p.start, p.pgid) for p in rows if p.pgid == pgid}
    expected_top = pgid if mode == 'alive' else -pgid
    expected = (expected_top, owned)
    got = watch.pick(rows, keep)
    details = dict(count=len(rows), limit=len(rows)-1, group=pgid, top=expected_top,
                   owned=len(owned), selected_top=got[0] if got else None,
                   selected_members=len(got[1]) if got else 0)
    with open(watch.state_file(env, 'live-snapshot.json'), 'w') as f:
        json.dump(details, f)
    validate(got, expected)
    return rows, main, now, expected


def validate(got, expected):
    top, owned = expected
    if not owned or not got or got[0] != top or {(p.pid, p.start, p.pgid) for p in got[1]} != owned:
        raise RuntimeError(f'watch would not select exactly the owned group {top}')


def tick_owned(watch, env, pgid, mode):
    rows, main, now, expected = capture(watch, env, pgid, mode)
    limit = len(rows) - 1
    print('live snapshot ' + Path(watch.state_file(env, 'live-snapshot.json')).read_text(), flush=True)
    read, pick, stop = watch.list_procs, watch.pick, watch.stop_group
    first = True

    def initial_then_fresh():
        nonlocal first
        if first:
            first = False
            return rows
        return read()

    def checked_pick(procs, keep, *args):
        got = pick(procs, keep, *args)
        validate(got, expected)
        return got

    def checked_stop(members, keep_for, *args, **kwargs):
        if not members or any((p.pid, p.start, p.pgid) not in expected[1] for p in members):
            raise RuntimeError('stop members differ from recorded owned processes')
        return stop(members, keep_for, *args, **kwargs)

    watch.list_procs, watch.pick, watch.stop_group = initial_then_fresh, checked_pick, checked_stop
    try:
        line = watch.tick(env, main, now, limit)
        if not line:
            raise RuntimeError('owned snapshot did not exercise the over-limit branch')
        return line
    finally:
        watch.list_procs, watch.pick, watch.stop_group = read, pick, stop


def main():
    spec = importlib.util.spec_from_file_location('fork_watch_live', HERE / 'fork-watch.py')
    watch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(watch)
    action, group, mode = sys.argv[1:]
    env = dict(os.environ)
    try:
        if action == 'ready':
            capture(watch, env, int(group), mode)
        elif action == 'tick':
            print(tick_owned(watch, env, int(group), mode))
        else:
            raise RuntimeError('unknown live-test action')
    except Exception as error:
        print(f'live fixture not ready: {error}', file=sys.stderr)
        for name in ('heartbeat', 'live-snapshot.json'):
            path = Path(watch.state_file(env, name))
            if path.exists():
                print(f'{name}: {path.read_text().strip()}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
