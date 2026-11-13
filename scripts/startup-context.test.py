"""Contracts for process-local startup reduction; no installed files are changed."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class StartupContextTest(unittest.TestCase):
    def launch_settings(self, role):
        # Only DNS and the external CLI are replaced; the real launcher merges
        # settings and passes the resulting process-local arguments unchanged.
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            bin_ = home / 'bin'
            bin_.mkdir()
            for name, body in {
                'dscacheutil': 'printf "ip_address: 127.0.0.1\\n"',
                'claude': 'printf "%s\\n" "$@"',
            }.items():
                path = bin_ / name
                path.write_text('#!/bin/sh\n' + body + '\n')
                path.chmod(0o755)
            env = dict(os.environ, HOME=str(home), XDG_STATE_HOME=str(home / 'state'),
                       PATH=str(bin_) + os.pathsep + os.environ['PATH'], HERDR_PANE_ID='',
                       CLAUDE_STARTUP_ROLE=role, CLAUDE_STARTUP_PROFILE='',
                       CLAUDE_STARTUP_ARTIFACT='', CLAUDE_STARTUP_SKILLS='',
                       CLAUDE_CHECK_TARGET='')
            args = subprocess.check_output(['bash', str(ROOT / 'scripts/claude-online')],
                                           cwd=home, env=env, text=True).splitlines()
            return json.loads(args[args.index('--settings') + 1])

    def test_general_roles_exclude_six_connections_without_overriding_safety_hooks(self):
        for role in ('main', 'general', 'check'):
            with self.subTest(role=role):
                settings = self.launch_settings(role)
                self.assertLessEqual(settings['skillListingBudgetFraction'], .0015)
                self.assertLessEqual(settings['skillListingMaxDescChars'], 160)
                self.assertEqual({s['serverName'] for s in settings['deniedMcpServers']},
                                 {'rea', 'Amplitude', 'notion-token', 'obsidian',
                                  'plugin:figma:figma', 'plugin:discord:discord'})
                self.assertNotIn('hooks', settings)
                self.assertNotEqual(settings['enabledPlugins'].get('ponytail@ponytail'), False)

    def test_worker_does_not_block_delegated_amplitude_notion_or_figma_connections(self):
        settings = self.launch_settings('worker')
        self.assertEqual({s['serverName'] for s in settings['deniedMcpServers']},
                         {'rea', 'obsidian'})
        self.assertNotIn('hooks', settings)
        self.assertEqual(settings['permissions'],
                         {'deny': ['AskUserQuestion', 'ScheduleWakeup', 'SendFeedback']})


if __name__ == '__main__':
    unittest.main()
