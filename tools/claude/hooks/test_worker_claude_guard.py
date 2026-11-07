#!/usr/bin/env python3
"""Exercise the real PreToolUse entry point; only herdr is a local fixture."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HOOK = Path(__file__).with_name('no-sed-inplace.py')


class WorkerClaudeGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        fake = root / 'herdr'
        fake.write_text('''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
if args[:2] == ['pane', 'get']:
    result = {'pane': {'pane_id': 'current', 'terminal_id': 'term_worker'}}
elif args == ['agent', 'get', 'current']:
    result = {'agent': {'name': os.environ.get('FIXTURE_AGENT', 'spawn-lead-check')}}
else:
    sys.exit(2)
print(json.dumps({'result': result}))
''')
        fake.chmod(0o700)
        self.env = dict(os.environ, HOME=str(root), XDG_STATE_HOME=str(root / 'state'),
                        HERDR_BIN_PATH=str(fake), HERDR_PANE_ID='stale',
                        HERDR_SOCKET_PATH='/fixture/sessions/workers/herdr.sock')
        self.main = root / 'state/orchestrator/main'
        self.main.parent.mkdir(parents=True)

    def decision(self, command, tool='Bash'):
        result = subprocess.run([sys.executable, '-I', str(HOOK)],
                                input=json.dumps({'tool_name': tool, 'tool_input': {'command': command}}),
                                text=True, capture_output=True, env=self.env, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)['hookSpecificOutput'] if result.stdout.strip() else {}

    def test_worker_model_commands_are_denied_before_they_run(self):
        for command in ['claude -p test', 'claude --print test', 'claude --print=test',
                        'claude "test prompt"', 'claude --model opus "test prompt"',
                        'printf test | claude', 'claude < prompt.txt',
                        '/opt/bin/claude -p test', 'env TEST=1 claude -p test',
                        'timeout 5 claude -p test', 'command claude -p test',
                        'bash -lc \'claude -p test\'', 'result=$(claude -p test)',
                        'echo "$(claude -p test)"', 'cl -p test', 'cla -p test']:
            with self.subTest(command=command):
                decision = self.decision(command)
                self.assertEqual(decision.get('permissionDecision'), 'deny')
                self.assertIn('probe.py', decision['permissionDecisionReason'])

    def test_worker_safe_commands_and_normal_pane_start_are_allowed(self):
        for command in ['claude --version', 'claude -v', 'claude --help', 'claude mcp list',
                        'claude auth status', 'claude plugin list',
                        'pane-spawn "task" "instruction" --kind claude --why test',
                        'python3 scripts/claude-probe/probe.py --cwd .',
                        'echo "claude -p test"', "echo '$(claude -p test)'",
                        "cat <<'EOF'\nclaude -p test\nEOF", '# claude -p test\nprintf ok']:
            with self.subTest(command=command):
                self.assertEqual(self.decision(command), {})

    def test_nested_captures_and_runner_options_cannot_hide_requests(self):
        for command in ["bash -lc 'result=$(claude -p test)'",
                        "bash -lc 'result=`claude -p test`'",
                        'env -u SOME_VAR claude -p test',
                        "timeout 5 env TEST=1 bash -lc 'claude -p test'"]:
            with self.subTest(command=command):
                self.assertEqual(self.decision(command).get('permissionDecision'), 'deny')

    def test_comment_capture_is_text(self):
        self.assertEqual(self.decision('# $(claude -p test)\nprintf ok'), {})

    def test_process_substitution_and_expanding_heredoc_run_commands(self):
        for command in ['cat <(claude -p test)', 'cat <<EOF\n$(claude -p test)\nEOF']:
            with self.subTest(command=command):
                self.assertEqual(self.decision(command).get('permissionDecision'), 'deny')
        self.assertEqual(self.decision('echo "<(claude -p test)"'), {})

    def test_shell_heredoc_is_executable_even_with_quoted_delimiter(self):
        self.assertEqual(self.decision("bash <<'EOF'\nclaude -p test\nEOF").get('permissionDecision'), 'deny')

    def test_manual_worker_pane_is_allowed(self):
        self.env['FIXTURE_AGENT'] = ''
        self.assertEqual(self.decision('claude -p test'), {})

    def test_dispatch_job_is_not_a_pane_spawn_worker(self):
        self.env['FIXTURE_AGENT'] = 'auto-api-123'
        self.assertEqual(self.decision('claude -p test'), {})

    def test_main_pane_is_allowed(self):
        self.main.write_text('term_worker\n')
        self.assertEqual(self.decision('claude -p test'), {})

    def test_other_server_and_launchd_without_pane_are_allowed(self):
        self.env['HERDR_SOCKET_PATH'] = '/fixture/sessions/default/herdr.sock'
        self.assertEqual(self.decision('claude -p test'), {})
        self.env.pop('HERDR_PANE_ID')
        self.assertEqual(self.decision('claude -p test'), {})

    def test_non_bash_tool_is_outside_this_guard(self):
        self.assertEqual(self.decision('claude -p test', 'Read'), {})

    def test_unknown_herdr_state_does_not_stop_unrelated_work(self):
        self.env['HERDR_BIN_PATH'] = '/fixture/missing-herdr'
        self.assertEqual(self.decision('claude -p test'), {})


if __name__ == '__main__':
    if '--selftest' in sys.argv:  # discovered by the repository's pre-push runner
        sys.argv.remove('--selftest')
    unittest.main()
