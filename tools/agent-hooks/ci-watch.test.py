#!/usr/bin/env python3
"""Exercise the real CLI with a local gh stand-in; no network or Actions starts."""
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ENTRY = Path(__file__).with_name('ci-watch.py')


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
        finally:
            os.kill(child_pid, signal.SIGTERM)


if __name__ == '__main__':
    unittest.main()
