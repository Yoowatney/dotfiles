#!/usr/bin/env python3
"""settings-drift.py against a fake ssh and bash in a temporary HOME."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).with_name('settings-drift.py')
DONE = 'VERIFY-DONE\n'


class DriftTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        home, self.fake = root / 'home', root / 'fake'
        (home / '.config/herdr').mkdir(parents=True)
        (home / '.config/munice').mkdir(parents=True)
        self.fake.mkdir()
        (home / '.config/herdr/remote-host').write_text('homehost\n')
        self.portable = home / '.config/munice/portable-host'
        self.portable.write_text('homehost\n')
        for name, body in (('ssh', 'cat "$FAKE/ssh.out"; exit "$(cat "$FAKE/ssh.code")"'), ('bash', 'cat "$FAKE/local.out"')):
            (self.fake / name).write_text('#!/bin/bash\n' + body + '\n')
            (self.fake / name).chmod(0o755)
        (self.fake / 'local.out').write_text('세션맥\n' + DONE)
        self.remote('집맥\n' + DONE)
        self.states = root / 'state'
        self.env = {**os.environ, 'HOME': str(home), 'XDG_STATE_HOME': str(self.states), 'FAKE': str(self.fake),
                    'SETTINGS_DRIFT_SSH': str(self.fake / 'ssh'), 'SETTINGS_DRIFT_BASH': str(self.fake / 'bash')}
        self.state = self.states / 'settings-drift/state.json'

    def tearDown(self):
        self.tmp.cleanup()

    def remote(self, out, code=0):
        (self.fake / 'ssh.out').write_text(out)
        (self.fake / 'ssh.code').write_text(str(code))

    def drift(self):
        return subprocess.run(['/usr/bin/python3', '-I', str(SCRIPT)], env=self.env, capture_output=True, text=True)

    def alerts(self):
        inbox = self.states / 'orchestrator/alerts/state.json'
        return list(json.loads(inbox.read_text())['events'].values()) if inbox.exists() else []

    def target(self):
        return json.loads(self.state.read_text())['homehost']

    def test_new_failure_alerts_once_and_clears_when_fixed(self):
        self.remote('집맥\nVERIFY-FAIL snap apps\n' + DONE, 1)
        self.assertEqual(self.drift().returncode, 0)
        self.assertEqual(len(self.alerts()), 1)
        self.assertIn('집맥: snap apps', self.alerts()[0]['body'])
        self.drift()
        self.assertEqual(len(self.alerts()), 1)
        self.remote('집맥\n' + DONE)
        self.drift()
        self.assertEqual(self.target()['fails'], [])

    def test_failure_back_after_a_fix_alerts_again_the_same_day(self):
        failing = '집맥\nVERIFY-FAIL snap apps\n' + DONE
        self.remote(failing, 1)
        self.drift()
        self.remote('집맥\n' + DONE)
        self.drift()
        self.remote(failing, 1)
        self.drift()
        self.assertEqual(len(self.alerts()), 2)

    def test_runs_only_on_the_mac_with_portable_host(self):
        self.portable.unlink()
        self.remote('집맥\nVERIFY-FAIL snap apps\n' + DONE, 1)
        self.assertEqual(self.drift().returncode, 0)
        self.assertFalse(self.state.exists())
        self.assertEqual(self.alerts(), [])

    def test_unreachable_host_keeps_last_ok_and_stays_quiet(self):
        self.drift()
        before = self.target()['last_ok']
        self.remote('', 255)
        self.assertEqual(self.drift().returncode, 0)
        self.assertEqual(self.alerts(), [])
        self.assertEqual(self.target()['last_ok'], before)

    def test_missing_verify_done_is_a_failed_run(self):
        self.remote('집맥\nVERIFY-FAIL snap apps\n', 1)
        self.drift()
        self.assertEqual(len(self.alerts()), 1)
        self.assertIn('점검 실행 실패', self.alerts()[0]['body'])
        self.drift()
        self.assertEqual(len(self.alerts()), 1)

    def test_host_unchecked_for_8_days_alerts_once(self):
        self.drift()
        data = json.loads(self.state.read_text())
        data['homehost']['last_ok'] -= 8 * 86400
        self.state.write_text(json.dumps(data))
        self.remote('', 255)
        self.drift()
        self.assertEqual(len(self.alerts()), 1)
        self.assertIn('8일', self.alerts()[0]['body'])
        self.drift()
        self.assertEqual(len(self.alerts()), 1)

    def test_inbox_failure_exits_1_without_saving_state(self):
        self.states.mkdir()
        (self.states / 'orchestrator').write_text('not a directory')
        self.remote('집맥\nVERIFY-FAIL snap apps\n' + DONE, 1)
        self.assertEqual(self.drift().returncode, 1)
        self.assertFalse(self.state.exists())


if __name__ == '__main__':
    unittest.main()
