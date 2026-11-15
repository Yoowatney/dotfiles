#!/usr/bin/env python3
"""Use isolated plists; never talk to launchctl or the user's LaunchAgents."""
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shlex
import subprocess
import sys
import tempfile
import unittest

INSTALLER = Path(__file__).with_name('install-scheduled-job-health.py')
RUNNER = INSTALLER.with_name('scheduled-job-health.py')


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.agents = self.root / 'LaunchAgents'
        self.agents.mkdir()
        self.env = {**os.environ, 'HOME': str(self.root), 'XDG_STATE_HOME': str(self.root / 'state')}
        self.original = {'Label': 'test.hourly', 'ProgramArguments': ['/bin/sh', '-c', 'exit 3'],
                         'StartInterval': 3600, 'EnvironmentVariables': {'EXAMPLE': 'value'},
                         'StandardOutPath': str(self.root / 'out.log')}
        self.path = self.agents / 'hourly.plist'
        self.path.write_bytes(plistlib.dumps(self.original))

    def call(self, *args):
        result = subprocess.run([sys.executable, str(INSTALLER), '--agents-dir', str(self.agents),
                                 '--prefix', 'test.', *args], env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def test_inventory_is_read_only_and_excludes_services_and_foreign_jobs(self):
        for name, data in [('service', {'Label': 'test.service', 'KeepAlive': True, 'ProgramArguments': ['/bin/true']}),
                           ('foreign', {'Label': 'foreign.hourly', 'StartInterval': 5, 'ProgramArguments': ['/bin/true']})]:
            (self.agents / f'{name}.plist').write_bytes(plistlib.dumps(data))
        before = self.path.read_bytes()
        result = self.call()
        jobs = json.loads(result.stdout)['jobs']
        self.assertEqual([job['label'] for job in jobs], ['test.hourly'])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse((self.root / 'state').exists())

    def test_install_preserves_configuration_and_original_exit_and_is_idempotent(self):
        self.call('--apply')
        installed = plistlib.loads(self.path.read_bytes())
        for key in ['Label', 'StartInterval', 'EnvironmentVariables', 'StandardOutPath']:
            self.assertEqual(installed[key], self.original[key])
        result = subprocess.run(installed['ProgramArguments'], env=self.env, capture_output=True)
        self.assertEqual(result.returncode, 3)
        before = self.path.read_bytes()
        self.call('--apply')
        self.assertEqual(self.path.read_bytes(), before)
        self.call('--restore')
        self.assertEqual(plistlib.loads(self.path.read_bytes()), self.original)

    def test_restore_recreates_symlink_without_mutating_its_target(self):
        original = self.root / 'tracked.plist'
        original.write_bytes(self.path.read_bytes())
        self.path.unlink()
        self.path.symlink_to(original)
        before = original.read_bytes()
        self.call('--apply')
        self.assertFalse(self.path.is_symlink())
        self.assertEqual(original.read_bytes(), before)
        self.call('--restore')
        self.assertTrue(self.path.is_symlink())
        self.assertEqual(self.path.readlink(), original)

    def test_program_only_and_disabled_jobs_are_not_silently_rewritten(self):
        self.path.write_bytes(plistlib.dumps({'Label': 'test.program', 'Program': '/bin/true', 'StartInterval': 60}))
        disabled = self.agents / 'disabled.plist'
        disabled.write_bytes(plistlib.dumps({'Label': 'test.disabled', 'Disabled': True,
                                            'StartInterval': 60, 'ProgramArguments': ['/bin/true']}))
        result = self.call()
        self.assertEqual(json.loads(result.stdout)['jobs'], [])

    def test_restore_preserves_original_permissions(self):
        self.path.chmod(0o644)
        self.call('--apply')
        self.call('--restore')
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o644)

    def test_apply_recovers_a_backup_saved_before_plist_replacement(self):
        original = self.path.read_bytes()
        self.call('--apply')
        self.path.write_bytes(original)
        self.call('--apply')
        self.assertNotEqual(self.path.read_bytes(), original)
        self.call('--restore')
        self.assertEqual(self.path.read_bytes(), original)

    def test_empty_keepalive_dictionary_is_still_a_service(self):
        self.original['KeepAlive'] = {}
        self.path.write_bytes(plistlib.dumps(self.original))
        self.assertEqual(json.loads(self.call().stdout)['jobs'], [])

    def test_watch_profile_catches_whole_run_skip_but_not_item_skips(self):
        script = self.root / 'watch/run.sh'
        script.parent.mkdir()
        actual_log = self.root / 'out.log'
        script.write_text(f"#!/bin/sh\necho 'item skipped' >> '{actual_log}'\n")
        self.original['ProgramArguments'] = ['/bin/sh', str(script)]
        self.original['StandardOutPath'] = str(self.root / 'out.launchd.log')
        self.path.write_bytes(plistlib.dumps(self.original))
        self.call('--apply')
        command = plistlib.loads(self.path.read_bytes())['ProgramArguments']
        subprocess.run(command, env=self.env, check=True)
        subprocess.run(command, env=self.env, check=True)
        inbox = self.root / 'state/orchestrator/alerts/state.json'
        self.assertFalse(inbox.exists())
        script.write_text(f"#!/bin/sh\necho '2026-01-01 offline, skipped' >> '{actual_log}'\n")
        subprocess.run(command, env=self.env, check=True)
        subprocess.run(command, env=self.env, check=True)
        self.assertEqual(len(json.loads(inbox.read_text())['events']), 1)

    def assert_profile_outcome(self, program, message, expected):
        # Exercise the installer's real profile, then the runner and inbox together.
        with tempfile.TemporaryDirectory(dir=self.root) as temporary:
            root = Path(temporary)
            env = {**self.env, 'HOME': str(root), 'XDG_STATE_HOME': str(root / 'state')}
            script = root / program
            script.parent.mkdir(parents=True, exist_ok=True)
            launchd_log = root / 'out.launchd.log'
            separate_log = program.endswith('run.sh') or program == 'sync-home.sh'
            actual_log = launchd_log.with_name('out.log') if separate_log else launchd_log
            script.write_text('#!/bin/sh\nprintf "%s\\n" ' + shlex.quote(message) +
                              ' >> ' + shlex.quote(str(actual_log)) + '\n')
            agents = root / 'LaunchAgents'
            agents.mkdir()
            plist = {'Label': 'test.profile', 'StartInterval': 60,
                     'ProgramArguments': ['/bin/sh', str(script)], 'StandardOutPath': str(launchd_log)}
            (agents / 'job.plist').write_bytes(plistlib.dumps(plist))
            inventory = subprocess.run([sys.executable, str(INSTALLER), '--agents-dir', str(agents),
                                        '--prefix', 'test.'], env=env, capture_output=True, text=True, check=True)
            logs = json.loads(inventory.stdout)['jobs'][0]['logs']
            self.assertTrue(logs, program)
            spec = root / 'spec.json'
            spec.write_text(json.dumps({'label': plist['Label'], 'command': plist['ProgramArguments'], 'logs': logs}))
            inbox = root / 'state/orchestrator/alerts/state.json'
            state = root / 'state/scheduled-job-health/runs' / hashlib.sha256(b'test.profile').hexdigest()[:24] / 'state.json'
            for attempt in range(1, 4):
                subprocess.run([sys.executable, str(RUNNER), 'run', '--spec', str(spec)], env=env, check=True)
                events = json.loads(inbox.read_text())['events'] if inbox.exists() else {}
                self.assertEqual(len(events), int(expected == 'skipped' and attempt >= 2), (message, attempt))
                recorded = json.loads(state.read_text())
                self.assertEqual(recorded['recent'][-1]['outcome'], expected, message)
                self.assertEqual(recorded['streak'], attempt if expected == 'skipped' else 0)

    def test_dispatch_profile_alerts_once_for_every_whole_run_skip(self):
        for message in ['late slot (14h KST), skipped', 'another dispatch run is going, skipped',
                        'offline, skipped', 'herdr not reachable, skipped', 'no herdr workspace, skipped',
                        'future whole-run reason, skipped',
                        'late slot (13h KST) by the time of opening, nothing opened']:
            with self.subTest(message=message):
                self.assert_profile_outcome('dispatch/run.sh', message, 'skipped')

    def test_dispatch_profile_does_not_count_item_skips_or_empty_work(self):
        for message in ['not a board url, skipped: example', 'not seen working, goal skipped: example',
                        'nothing to open; all candidates accounted for']:
            with self.subTest(message=message):
                self.assert_profile_outcome('dispatch/run.sh', message, 'success')

    def test_watch_profile_accepts_whole_run_skip_format(self):
        for message in ['offline, skipped', 'future whole-run reason, skipped']:
            with self.subTest(message=message):
                self.assert_profile_outcome('watch/run.sh', message, 'skipped')

    def test_sync_profile_covers_whole_run_skips_and_successful_partial_noops(self):
        for reason in ['this is the other host', 'not cut over yet', 'another sync held the lock for 60 s',
                       'no ssh target', 'remote unreachable']:
            with self.subTest(reason=reason):
                self.assert_profile_outcome('sync-home.sh', '2026-01-01 skip: ' + reason, 'skipped')
        self.assert_profile_outcome('sync-home.sh', 'skip: one step\nok', 'success')

    def test_collection_lock_skip_is_a_whole_run_skip(self):
        self.assert_profile_outcome('pinned_post.py', '2026-01-01T00:00:00 post skipped: collection lock busy', 'skipped')

    def test_restore_recovers_when_original_was_restored_before_backup_cleanup(self):
        original = self.path.read_bytes()
        self.call('--apply')
        self.path.write_bytes(original)
        self.call('--restore')
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list((self.root / 'state/scheduled-job-health/installed').glob('*.json')), [])

    def test_restore_refuses_to_overwrite_changes_made_after_install(self):
        self.call('--apply')
        data = plistlib.loads(self.path.read_bytes())
        data['StartInterval'] = 5
        self.path.write_bytes(plistlib.dumps(data))
        result = subprocess.run([sys.executable, str(INSTALLER), '--agents-dir', str(self.agents),
                                 '--prefix', 'test.', '--restore'], env=self.env, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(plistlib.loads(self.path.read_bytes())['StartInterval'], 5)


if __name__ == '__main__':
    unittest.main()
