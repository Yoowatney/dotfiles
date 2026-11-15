#!/usr/bin/env python3
"""Use isolated plists; never talk to launchctl or the user's LaunchAgents."""
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest

INSTALLER = Path(__file__).with_name('install-scheduled-job-health.py')


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
