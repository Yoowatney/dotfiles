#!/usr/bin/env python3
"""Safe regressions for the live watchdog test's preconditions and snapshot use."""
import importlib.util
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class UidLockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.children = []
        self.addCleanup(self.stop_children)
        source = (HERE / 'fork-watch.test.sh').read_text()
        prefix = source[:source.index('\nHERE=')]
        lock_source = '/tmp/dotfiles-fork-watch-{os.getuid()}.lock'
        self.assertEqual(prefix.count(lock_source), 1)
        lock_dir = self.root / 'locks'
        lock_dir.mkdir()
        self.lock_path = lock_dir / f'dotfiles-fork-watch-{os.getuid()}.lock'
        # Keep the UID key outside both TMPDIRs without contending with live tests.
        prefix = prefix.replace(lock_source, str(lock_dir) + '/dotfiles-fork-watch-{os.getuid()}.lock')
        # Execute the real locking entry point with a small critical section.
        # Each copy has a different TMPDIR, just like independent pre-push runs.
        self.script = self.root / 'fixture.sh'
        self.script.write_text(prefix + '''
exec /usr/bin/python3 -I - "$FORK_FIXTURE_DIR" "$FORK_FIXTURE_NAME" "$FORK_FIXTURE_EXIT" <<'PY'
from pathlib import Path
import sys, time
root, name, code = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
(root / (name + '.entered')).touch()
deadline = time.monotonic() + 30
while not (root / (name + '.release')).exists():
    if time.monotonic() >= deadline:
        raise SystemExit(99)
    time.sleep(.02)
raise SystemExit(code)
PY
''')

    def stop_children(self):
        for proc, output in self.children:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=10)
            output.close()

    def start(self, name, code=0):
        tmpdir = self.root / ('tmp-' + name)
        tmpdir.mkdir()
        output = (self.root / (name + '.log')).open('w+')
        env = dict(os.environ, TMPDIR=str(tmpdir), FORK_FIXTURE_DIR=str(self.root),
                   FORK_FIXTURE_NAME=name, FORK_FIXTURE_EXIT=str(code))
        proc = subprocess.Popen(['/bin/bash', str(self.script)], env=env,
                                stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        self.children.append((proc, output))
        return proc

    def wait_for(self, condition):
        deadline = time.monotonic() + 30
        while not condition():
            self.assertLess(time.monotonic(), deadline, 'fixture condition was not reached')
            time.sleep(.02)

    def entered(self, name):
        return (self.root / (name + '.entered')).exists()

    def waiting(self, name):
        return 'Waiting for another fork-watch live test' in (self.root / (name + '.log')).read_text()

    def pair(self, code=0):
        first = self.start('first', code)
        self.wait_for(lambda: self.entered('first'))
        with self.lock_path.open('a') as probe:
            with self.assertRaises(BlockingIOError):
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        second = self.start('second')
        self.wait_for(lambda: self.waiting('second') or self.entered('second'))
        self.assertTrue(self.waiting('second'))
        self.assertFalse(self.entered('second'))
        return first, second

    def finish(self, proc, name, code=0):
        (self.root / (name + '.release')).touch()
        self.assertEqual(proc.wait(timeout=10), code)

    def test_different_tmpdirs_share_one_uid_lock(self):
        first, second = self.pair()
        self.finish(first, 'first')
        self.wait_for(lambda: self.entered('second'))
        self.finish(second, 'second')

    def test_failed_owner_releases_lock_and_preserves_exit_status(self):
        first, second = self.pair(7)
        self.finish(first, 'first', 7)
        self.wait_for(lambda: self.entered('second'))
        self.finish(second, 'second')

    def test_killed_owner_releases_lock(self):
        first, second = self.pair()
        os.killpg(first.pid, signal.SIGKILL)
        self.assertEqual(first.wait(timeout=10), -signal.SIGKILL)
        self.wait_for(lambda: self.entered('second'))
        self.finish(second, 'second')


class ReadinessTests(unittest.TestCase):
    def test_exhausted_readiness_never_starts_watch(self):
        source = (HERE / 'fork-watch.test.sh').read_text()
        start = source[source.index('start() {'):source.index('\nwatch() {')]
        fixture = '''#!/bin/bash
set -uo pipefail
function /usr/bin/python3() { return 1; }
users() { echo 2427; }
left() { echo 1221; }
sleep() { :; }
pkill() { echo UNEXPECTED_KILL >&2; return 99; }
cap=2488 lim=2428 stop=2588 bomb=0
HERE=/unused
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
''' + start + '''
start; rc=$?
wait
exit "$rc"
'''
        result = subprocess.run(['/bin/bash'], input=fixture, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('not ready', result.stdout + result.stderr)


class SnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.watch = load('fork_watch_fixture', 'fork-watch.py')
        cls.helper = load('fork_watch_live_fixture', 'fork-watch-live.py')

    def setUp(self):
        # Synthetic process identities must never match this test runner.
        identity = mock.patch.object(self.watch.os, 'getpid', return_value=999999)
        identity.start()
        self.addCleanup(identity.stop)

    def rows(self, count=2427):
        P = self.watch.Proc
        return ([P(1000000+i, 1, 4373, 1000000+i, 2, 'fixture', 0) for i in range(1221)] +
                [P(2000000+i, 1, 2000000+i, 2000000+i, 2, 'fixture', 0) for i in range(count-1221)])

    def test_product_fixed_threshold_and_orphan_selection(self):
        w = self.watch
        for count, stopped in [(2427, False), (2428, False), (2429, True)]:
            with self.subTest(count=count), tempfile.TemporaryDirectory() as tmp, \
                 mock.patch.object(w, 'list_procs', return_value=self.rows(count)), \
                 mock.patch.object(w, 'argv_of', return_value=[]), \
                 mock.patch.object(w, 'stop_group', side_effect=lambda members, keep: members) as stop, \
                 mock.patch.object(w, 'tell_main', return_value=True), \
                 mock.patch.object(w, 'notify', return_value=True):
                got = w.pick(self.rows(count), set())
                self.assertEqual((got[0], len(got[1])), (-4373, 1221))
                line = w.tick({'XDG_STATE_HOME': tmp}, w.Main(ask=lambda env: 0), 1000, 2428)
                self.assertEqual(bool(line), stopped)
                self.assertEqual(stop.called, stopped)

    def test_initial_snapshot_is_frozen_but_stop_reads_fresh_table(self):
        w, h = self.watch, self.helper
        initial, later = self.rows(), self.rows(2429)
        fresh_counts = []
        reads, terminated, signals = [], [], []
        def read_table():
            reads.append(1)
            if len(reads) == 1:
                return initial
            rows = [p for p in later if p.pgid != 4373] if terminated else later
            fresh_counts.append(len(rows))
            return rows
        def signal_rows(rows, sig):
            signals.append(sig)
            self.assertTrue(all(p.pgid == 4373 for p in rows))
            if sig == w.signal.SIGTERM:
                terminated.append(True)
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(w, 'list_procs', side_effect=read_table) as read, \
             mock.patch.object(w, 'argv_of', return_value=[]), \
             mock.patch.object(w, '_signal', side_effect=signal_rows), \
             mock.patch.object(w, 'tell_main', return_value=True), \
             mock.patch.object(w, 'notify', return_value=True):
            line = h.tick_owned(w, {'XDG_STATE_HOME': tmp}, 4373, 'orphan')
            self.assertIn('2,427개 > 2,426', line)
            self.assertIn('프로세스 그룹 4373(', line)
            self.assertEqual(fresh_counts[:3], [2429, 2429, 2429])
            self.assertEqual(fresh_counts[-2:], [1208, 1208])
            self.assertEqual(read.call_count, 6)
            self.assertEqual(signals, [w.signal.SIGSTOP, w.signal.SIGTERM, w.signal.SIGCONT])

    def test_another_group_is_rejected_before_tick(self):
        w, h = self.watch, self.helper
        P = w.Proc
        rows = self.rows() + [P(3000000+i, 1, 9999, 3000000+i, 2, 'fixture', 0) for i in range(1222)]
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(w, 'list_procs', return_value=rows), \
             mock.patch.object(w, 'argv_of', return_value=[]), \
             mock.patch.object(w, 'tick', side_effect=AssertionError('unsafe tick invoked')):
            with self.assertRaisesRegex(RuntimeError, 'owned group'):
                h.tick_owned(w, {'XDG_STATE_HOME': tmp}, 4373, 'orphan')
            details = json.loads(Path(w.state_file({'XDG_STATE_HOME': tmp}, 'live-snapshot.json')).read_text())
            self.assertEqual((details['count'], details['owned'], details['selected_top']),
                             (3649, 1221, -9999))

    def test_tick_selection_changed_by_protection_aborts_before_stop(self):
        w, h = self.watch, self.helper
        actual = w.pick
        calls = []
        def changed(rows, keep, *args):
            calls.append(1)
            return actual(rows, keep) if len(calls) == 1 else (rows[-1].pid, [rows[-1]])
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(w, 'list_procs', return_value=self.rows()), \
             mock.patch.object(w, 'argv_of', return_value=[]), \
             mock.patch.object(w, 'pick', side_effect=changed), \
             mock.patch.object(w, 'stop_group', side_effect=AssertionError('unsafe stop invoked')):
            with self.assertRaisesRegex(RuntimeError, 'owned group'):
                h.tick_owned(w, {'XDG_STATE_HOME': tmp}, 4373, 'orphan')

    def test_stop_members_must_retain_recorded_start_and_group(self):
        w, h = self.watch, self.helper
        rows = self.rows()
        for bad in (rows[0]._replace(start=999), rows[0]._replace(pgid=9999), rows[-1]):
            def tamper(env, main, now, limit):
                return w.stop_group([bad], lambda fresh: set())
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as tmp, \
                 mock.patch.object(w, 'list_procs', return_value=rows), \
                 mock.patch.object(w, 'argv_of', return_value=[]), \
                 mock.patch.object(w, 'tick', side_effect=tamper), \
                 mock.patch.object(w, 'stop_group', side_effect=AssertionError('unsafe stop invoked')):
                with self.assertRaisesRegex(RuntimeError, 'recorded owned'):
                    h.tick_owned(w, {'XDG_STATE_HOME': tmp}, 4373, 'orphan')


if __name__ == '__main__':
    unittest.main()
