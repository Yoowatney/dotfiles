#!/usr/bin/env python3
"""Exercise the real CLI with a local gh stand-in; no network or Actions starts."""
import contextlib
import fcntl
import importlib.util
import io
import json
import os
import signal
import socket
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ENTRY = Path(__file__).with_name('ci-watch.py')
SPEC = importlib.util.spec_from_file_location('ci_watch', ENTRY)
WATCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WATCH)


class WatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        gh = self.root / 'gh'
        gh.write_text('#!' + sys.executable + '\n' + '''import json, os, pathlib, sys, time
p = pathlib.Path(os.environ['CALLS'])
with p.open('a') as f:
    f.write(json.dumps(sys.argv[1:]) + '\\n')
if os.environ.get('PID_FILE'):
    pathlib.Path(os.environ['PID_FILE']).write_text(str(os.getpid()))
if os.environ.get('WAIT'):
    while not pathlib.Path(os.environ['WAIT']).exists(): time.sleep(.01)
sys.exit(int(os.environ.get('RESULT', '0')))
''')
        gh.chmod(0o755)
        self.env = {**os.environ, 'PATH': str(self.root) + os.pathsep + os.environ['PATH'],
                    'XDG_STATE_HOME': str(self.root / 'state'), 'CALLS': str(self.root / 'calls')}
        self.children = []
        home = self.root / 'home'
        config = home / '.config' / 'munice'
        config.mkdir(parents=True)
        (config / 'ambient-company-host').write_text(socket.gethostname().split('.')[0])
        self.env['HOME'] = str(home)

    def tearDown(self):
        for child in self.children:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)
        self.temp.cleanup()

    def start(self, repo='owner/repo', run='123', **env):
        child = subprocess.Popen([sys.executable, str(ENTRY), '--repo', repo, run],
                                 env={**self.env, **env}, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.children.append(child)
        return child

    def status(self):
        result = subprocess.run([sys.executable, str(ENTRY), '--repo', 'owner/repo',
                                 '--status', '123'], env=self.env, capture_output=True,
                                text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_status_without_history_does_not_start_gh(self):
        status = self.status()
        self.assertFalse(status['active'])
        self.assertEqual(status['conclusion'], 'not_started')
        self.assertIsNone(status['returncode'])
        self.assertIsNone(status['wrapper_pid'])
        self.assertIsNone(status['gh_pid'])
        self.assertFalse((self.root / 'calls').exists())

    def test_state_save_failure_after_start_preserves_child_exit(self):
        real_save = WATCH.save_state
        for failed_save in (2, 3):
            for result in (0, 17):
                with self.subTest(failed_save=failed_save, result=result):
                    calls = 0

                    def save(path, state):
                        nonlocal calls
                        calls += 1
                        if calls == failed_save:
                            raise PermissionError('synthetic-sensitive-detail')
                        real_save(path, state)

                    err = io.StringIO()
                    children = []
                    real_popen = subprocess.Popen

                    def start(*args, **kwargs):
                        child = real_popen(*args, **kwargs)
                        children.append(child)
                        return child

                    try:
                        with patch.dict(os.environ, {**self.env, 'RESULT': str(result)}, clear=True), \
                                patch.object(sys, 'argv', [str(ENTRY), '--repo', 'owner/repo', '123']), \
                                patch.object(WATCH, 'save_state', side_effect=save), \
                                patch.object(WATCH.subprocess, 'Popen', side_effect=start), \
                                contextlib.redirect_stderr(err):
                            self.assertEqual(WATCH.main(), result)
                    finally:
                        for child in children:
                            child.wait(timeout=5)
                    self.assertIn('PermissionError', err.getvalue())
                    self.assertNotIn('synthetic-sensitive-detail', err.getvalue())
                    self.assertFalse(self.status()['active'])

    def test_mirror_and_unknown_hosts_cannot_start_a_second_watcher(self):
        config = Path(self.env['HOME']) / '.config' / 'munice'
        for role in ('ambient-runner', 'portable', '', 'unknown'):
            with self.subTest(role=role):
                (config / 'host-role').write_text(role)
                child = self.start()
                _, err = child.communicate(timeout=5)
                self.assertEqual(child.returncode, 78, err)
        (config / 'host-role').unlink()
        for host in ('another-host', ''):
            with self.subTest(host=host):
                (config / 'ambient-company-host').write_text(host)
                child = self.start()
                _, err = child.communicate(timeout=5)
                self.assertEqual(child.returncode, 78, err)
        (config / 'ambient-company-host').unlink()
        child = self.start()
        child.communicate(timeout=5)
        self.assertEqual(child.returncode, 78)
        self.assertFalse((self.root / 'calls').exists())
        self.assertEqual(self.status()['conclusion'], 'not_started')

    def test_status_records_failed_result(self):
        child = self.start(RESULT='17')
        child.communicate(timeout=5)
        status = self.status()
        self.assertFalse(status['active'])
        self.assertEqual(status['returncode'], 17)
        self.assertEqual(status['conclusion'], 'completed')
        self.assertGreater(status['gh_pid'], 0)
        self.assertEqual(len((self.root / 'calls').read_text().splitlines()), 1)

    def test_missing_gh_is_recorded_as_start_failure(self):
        child = self.start(PATH=str(self.root / 'missing-bin'))
        _, err = child.communicate(timeout=5)
        self.assertEqual(child.returncode, 127, err)
        status = self.status()
        self.assertFalse(status['active'])
        self.assertEqual(status['returncode'], 127)
        self.assertIsNone(status['gh_pid'])

    def test_old_lock_without_saved_result_is_unknown(self):
        child = self.start()
        child.communicate(timeout=5)
        for state in (self.root / 'state' / 'ci-watch').glob('*.json'):
            state.unlink()
        self.assertEqual(self.status()['conclusion'], 'unknown')

    def test_broken_role_symlink_is_not_a_session_host(self):
        config = Path(self.env['HOME']) / '.config' / 'munice'
        (config / 'host-role').symlink_to(config / 'missing-role')
        child = self.start()
        child.communicate(timeout=5)
        self.assertEqual(child.returncode, 78)
        self.assertFalse((self.root / 'calls').exists())

    def test_polling_uses_sixty_seconds_and_preserves_ci_failure(self):
        child = self.start(RESULT='17')
        out, err = child.communicate(timeout=5)
        self.assertEqual(child.returncode, 17, err)
        self.assertEqual(json.loads((self.root / 'calls').read_text()),
                         ['run', 'watch', '123', '--repo', 'owner/repo', '--interval', '60', '--exit-status'])

    def test_duplicate_run_is_rejected_without_another_gh_process_then_lock_releases(self):
        release = self.root / 'release'
        first = self.start(WAIT=str(release))
        deadline = time.monotonic() + 5
        while not (self.root / 'calls').exists() and time.monotonic() < deadline:
            if first.poll() is not None:
                self.fail(first.communicate()[1])
            time.sleep(.01)
        duplicate = self.start(repo='OWNER/REPO')
        _, err = duplicate.communicate(timeout=5)
        self.assertEqual(duplicate.returncode, 75, err)
        self.assertIn('already', err)
        self.assertEqual(len((self.root / 'calls').read_text().splitlines()), 1)
        other = self.start(run='124')
        other.communicate(timeout=5)
        self.assertEqual(other.returncode, 0)
        release.touch()
        first.communicate(timeout=5)
        retry = self.start()
        retry.communicate(timeout=5)
        self.assertEqual(retry.returncode, 0)

    def test_invalid_identity_never_calls_gh(self):
        for repo, run in [('owner', '123'), ('owner/repo', '0'), ('owner/repo', '--interval=3')]:
            child = self.start(repo=repo, run=run)
            child.communicate(timeout=5)
            self.assertEqual(child.returncode, 2)
        self.assertFalse((self.root / 'calls').exists())

    def test_killed_wrapper_does_not_release_a_running_gh_lock(self):
        release = self.root / 'release'
        pid_file = self.root / 'gh.pid'
        first = self.start(WAIT=str(release), PID_FILE=str(pid_file))
        deadline = time.monotonic() + 5
        while not pid_file.exists() and time.monotonic() < deadline:
            if first.poll() is not None:
                self.fail(first.communicate()[1])
            time.sleep(.01)
        self.assertTrue(pid_file.exists())
        child_pid = int(pid_file.read_text())
        try:
            first.kill()
            first.wait(timeout=5)
            duplicate = self.start()
            _, err = duplicate.communicate(timeout=5)
            self.assertEqual(duplicate.returncode, 75, err)
            self.assertEqual(len((self.root / 'calls').read_text().splitlines()), 1)
            status = self.status()
            self.assertTrue(status['active'])
            self.assertEqual(status['gh_pid'], child_pid)
            self.assertEqual(status['wrapper_pid'], first.pid)
            self.assertIsNone(status['returncode'])
        finally:
            os.kill(child_pid, signal.SIGTERM)

        deadline = time.monotonic() + 5
        while self.status()['active'] and time.monotonic() < deadline:
            time.sleep(.01)
        status = self.status()
        self.assertFalse(status['active'])
        self.assertEqual(status['conclusion'], 'unknown')
        self.assertIsNone(status['returncode'])

    def test_corrupt_history_is_unknown_and_never_starts_gh(self):
        child = self.start()
        child.communicate(timeout=5)
        states = list((self.root / 'state' / 'ci-watch').glob('*.json'))
        self.assertEqual(len(states), 1)
        state = states[0]
        state.write_text('{')
        status = self.status()
        self.assertEqual(status['conclusion'], 'unknown')
        self.assertIsNone(status['returncode'])
        self.assertEqual(len((self.root / 'calls').read_text().splitlines()), 1)

    def test_boolean_result_is_unknown(self):
        child = self.start()
        child.communicate(timeout=5)
        state = next((self.root / 'state' / 'ci-watch').glob('*.json'))
        state.write_text(json.dumps({'returncode': True}))
        status = self.status()
        self.assertEqual(status['conclusion'], 'unknown')
        self.assertIsNone(status['returncode'])
        self.assertIsNone(status['wrapper_pid'])
        self.assertIsNone(status['gh_pid'])

    def test_status_reads_result_after_observing_released_lock(self):
        lock_path, state_path = self.root / 'watch.lock', self.root / 'watch.json'
        lock_path.touch()
        state_path.write_text(json.dumps({'returncode': None}))
        real_flock = fcntl.flock

        def finishing_watch(lock, operation):
            state_path.write_text(json.dumps({'returncode': 17}))
            return real_flock(lock, operation)

        with patch.object(WATCH.fcntl, 'flock', side_effect=finishing_watch):
            status = WATCH.status(lock_path, state_path)
        self.assertEqual(status['returncode'], 17)
        self.assertEqual(status['conclusion'], 'completed')


if __name__ == '__main__':
    unittest.main()
