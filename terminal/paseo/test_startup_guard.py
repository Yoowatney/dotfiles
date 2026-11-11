import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent


class StartupGuardTest(unittest.TestCase):
    def guard(self, command):
        result = subprocess.run([sys.executable, str(HERE / 'startup_guard.py')], input=json.dumps({
            'tool_name': 'Bash', 'tool_input': {'command': command}}), text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)['hookSpecificOutput']['permissionDecision'] if result.stdout else None

    def test_dirty_launches_are_denied(self):
        for command in ['open -a Paseo', '/usr/bin/open -g -a /Applications/Paseo.app',
                        'open -aPaseo', 'open --application=Paseo', 'paseo daemon start',
                        '/opt/homebrew/bin/paseo daemon start', 'command open -a Paseo',
                        'bash -lc "paseo daemon start"',
                        'env -i HOME=/tmp/test sh -c "export HERDR_PANE_ID=x; open -a Paseo"',
                        'env -i HOME="$HOME" HERDR_PANE_ID=w1:p1 open -a Paseo',
                        'env -i HOME="$HOME" open -a Paseo; paseo daemon start',
                        'echo "env -i"; open -a Paseo']:
            with self.subTest(command=command):
                self.assertEqual(self.guard(command), 'deny')

    def test_clean_and_unrelated_commands_pass(self):
        for command in ['env -i HOME="$HOME" USER="$USER" PATH=/usr/bin:/bin /usr/bin/open -g -a Paseo',
                        'env --ignore-environment HOME=/tmp/test PATH=/usr/bin:/bin paseo daemon start',
                        'env -i HOME=/tmp/test bash -c "open -a Paseo"',
                        'env -i HOME=/tmp/test PATH=/usr/bin:/bin sh -c "open -a Paseo"',
                        'paseo daemon status --json', 'paseo agent ls', 'open -a Safari',
                        'echo "open -a Paseo"', 'git commit -m "guard paseo daemon start"']:
            with self.subTest(command=command):
                self.assertIsNone(self.guard(command))

    def test_env_and_executable_wrappers_preserve_cleanliness(self):
        # Dropping a wrapper after env must not hide a dirty app or daemon launch.
        cases = [
            ('env HOME=/tmp/test nohup open -a Paseo', 'deny'),
            ('env HOME=/tmp/test nohup paseo daemon start', 'deny'),
            ('nohup env HOME=/tmp/test open -a Paseo', 'deny'),
            ('env -i HOME=/tmp/test HERDR_PANE_ID=test nohup open -a Paseo', 'deny'),
            ('env -i HOME=/tmp/test nohup env HERDR_PANE_ID=test paseo daemon start', 'deny'),
            ('env HOME=/tmp/test nohup env USER=test command open -a Paseo', 'deny'),
            ('env -i HOME=/tmp/test nohup open -a Paseo', None),
            ('nohup env -i HOME=/tmp/test paseo daemon start', None),
            ('/usr/bin/env -i HOME=/tmp/test /usr/bin/nohup /usr/bin/env USER=test open -a Paseo', None),
            ('env HERDR_PANE_ID=test nohup env -i HOME=/tmp/test open -a Paseo', None),
            ('env -i HOME=/tmp/test nohup sh -c "open -a Paseo"', None),
            ('env HOME=/tmp/test nohup paseo daemon status', None),
        ]
        for command, expected in cases:
            with self.subTest(command=command):
                self.assertEqual(self.guard(command), expected)

    def test_open_environment_cannot_reintroduce_session_identity(self):
        # A clean outer env is insufficient when open adds an unsafe variable.
        cases = [
            ('env -i HOME=/tmp/test open -a Paseo --env HERDR_PANE_ID=test', 'deny'),
            ('env -i HOME=/tmp/test open --env=HERDR_PANE_ID=test -a Paseo', 'deny'),
            ('env -i HOME=/tmp/test open -a Paseo --env HERDR_PANE_ID', 'deny'),
            ('env -i HOME=/tmp/test open -a Paseo --env CODEX_THREAD_ID=test', 'deny'),
            ('env -i HOME=/tmp/test open -a Paseo --env HOME=/tmp/test --env HERDR_SESSION=test', 'deny'),
            ('env -i HOME=/tmp/test nohup open -a Paseo --env HERDR_PANE_ID=test', 'deny'),
            ('env -i HOME=/tmp/test open -a Paseo --env', 'deny'),
            ('env -i HOME=/tmp/test open -a Paseo --env HOME=/tmp/test', None),
            ('env -i HOME=/tmp/test open -a Paseo --env=LC_ALL=C --env LANG=C', None),
            ('env -i HOME=/tmp/test open -a Paseo --args --env HERDR_PANE_ID=test', None),
            ('env -i HOME=/tmp/test open -a Safari --env HERDR_PANE_ID=test', None),
            ('open -- /Applications/Paseo.app', 'deny'),
            ('env -i HOME=/tmp/test open --env HERDR_PANE_ID=test -- /Applications/Paseo.app', 'deny'),
            ('env -i HOME=/tmp/test open -- /Applications/Paseo.app --env HERDR_PANE_ID=test', None),
        ]
        for command, expected in cases:
            with self.subTest(command=command):
                self.assertEqual(self.guard(command), expected)

    def test_installer_is_idempotent_preserves_positions_and_rejects_bad_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            paths = [home / '.claude/settings.json', home / '.codex/hooks.json']
            original = {'other': True, 'hooks': {'PreToolUse': [{'matcher': 'Bash', 'hooks': [{'type': 'command', 'command': 'existing'}]}]}}
            for path in paths:
                path.parent.mkdir()
                path.write_text(json.dumps(original))
            for _ in range(2):
                result = subprocess.run([sys.executable, str(HERE / 'startup_guard.py'), '--install', '--home', tmp], capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            for path in paths:
                data = json.loads(path.read_text())
                self.assertEqual(data['hooks']['PreToolUse'][0], original['hooks']['PreToolUse'][0])
                self.assertEqual(len(data['hooks']['PreToolUse']), 2)
                self.assertTrue(data['other'])
                command = data['hooks']['PreToolUse'][-1]['hooks'][0]['command']
                result = subprocess.run(shlex.split(command), input=json.dumps({
                    'tool_name': 'Bash', 'tool_input': {'cmd': 'paseo daemon start'}}),
                    capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)['hookSpecificOutput']['permissionDecision'], 'deny')
            paths[1].write_text('invalid')
            before = paths[0].read_bytes()
            result = subprocess.run([sys.executable, str(HERE / 'startup_guard.py'), '--install', '--home', tmp], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(paths[0].read_bytes(), before)
            self.assertEqual(paths[1].read_text(), 'invalid')


if __name__ == '__main__':
    unittest.main()
