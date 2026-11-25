import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('agy_hooks', Path(__file__).with_name('agy-hooks.py'))
hooks = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hooks)


def event(name='run_command', **args):
    return {'conversationId': 'test-conversation', 'cwd': '/tmp',
            'workspacePaths': ['/tmp'], 'toolCall': {'name': name, 'args': args}}


class NativeHooksTest(unittest.TestCase):
    def test_maps_commands_and_file_content(self):
        p = hooks.translate(event(CommandLine='printf OK'))
        self.assertEqual(p['tool_name'], 'Bash')
        self.assertEqual(p['tool_input']['command'], 'printf OK')
        p = hooks.translate(event('replace_file_content', TargetFile='/tmp/test',
                                  TargetContent='old', ReplacementContent='new'))
        self.assertEqual(p['tool_input']['new_string'], 'new')
        self.assertEqual(hooks.translate(event(CommandLine='pwd', Cwd='/private/tmp'))['cwd'], '/private/tmp')

    def test_unknown_and_nested_tools_are_denied(self):
        for name in ['unknown', 'invoke_subagent', 'call_mcp_tool', 'send_command_input', 'run_workflow']:
            with self.subTest(name=name):
                self.assertEqual(hooks.pre_tool(event(name))['decision'], 'deny')
        self.assertEqual(hooks.pre_tool({})['decision'], 'deny')

    def test_missing_command_is_denied(self):
        self.assertEqual(hooks.pre_tool(event())['decision'], 'deny')
        self.assertEqual(hooks.pre_tool(event(CommandLine='printf OK', RequestedTerminalID='another-terminal'))['decision'], 'deny')

    def test_stop_only_handles_declared_worker(self):
        with patch.object(hooks, 'load_completion') as loader:
            self.assertEqual(hooks.completion({}, {}), {'decision': 'allow'})
            loader.assert_not_called()
            payload = {'conversationId': 'test-conversation', 'fullyIdle': False}
            env = {'HERDR_AGY_STARTUP_ROLE': 'worker'}
            self.assertEqual(hooks.completion(payload, env), {'decision': 'allow'})
            loader.return_value.handle_stop.assert_called_once_with(payload, env)

    def test_existing_secret_guard_blocks_native_read(self):
        for name, args in [('view_file', {'AbsolutePath': '/tmp/.env'}),
                           ('read_file', {'Path': '/tmp/.env'})]:
            result = hooks.pre_tool(event(name, **args))
            self.assertEqual(result['decision'], 'deny')
            self.assertIn('secret-print-guard', result['reason'])

    def test_existing_gcp_guard_blocks_native_identity_change(self):
        result = hooks.pre_tool(event(CommandLine='gcloud auth login'))
        self.assertEqual(result['decision'], 'deny')
        self.assertIn('gcp-account-guard', result['reason'])

    def test_native_deny_and_slack(self):
        for cmd in ['git reset --hard HEAD', 'curl https://slack.com/api/chat.postMessage',
                    'python3 main-channel.py post hello']:
            with self.subTest(cmd=cmd):
                self.assertEqual(hooks.pre_tool(event(CommandLine=cmd))['decision'], 'deny')

    def test_shell_cannot_start_unprotected_agents(self):
        for cmd in ['agy --settings /tmp/empty.json -p hi',
                    "bash -c 'codex exec hi'", 'env X=1 claude -p hi',
                    'printf "%s" "$(agy -p hi)"']:
            with self.subTest(cmd=cmd):
                self.assertEqual(hooks.pre_tool(event(CommandLine=cmd))['decision'], 'deny')
        self.assertEqual(hooks.pre_tool(event(CommandLine='agy --version'))['decision'], 'allow')

    def test_safe_command_is_delegated_without_auto_approval(self):
        result = hooks.pre_tool(event(CommandLine='printf OK'))
        self.assertEqual(result['decision'], 'allow')

    def test_guard_failure_and_invalid_output_fail_closed(self):
        payload = hooks.translate(event(CommandLine='printf OK'))
        for command in ["exit 7", "printf not-json", "printf '{\"error\":\"broken\"}'"]:
            self.assertIsNotNone(hooks.run_guard(command, payload, timeout=1))
        self.assertIsNotNone(hooks.run_guard('sleep 2', payload, timeout=0.01))

    def test_cli_malformed_input_still_emits_deny(self):
        p = subprocess.run([sys.executable, '-I', str(Path(__file__).with_name('agy-hooks.py')), 'PreToolUse'],
                           input='not-json', text=True, capture_output=True)
        self.assertEqual(json.loads(p.stdout)['decision'], 'deny')
        self.assertNotIn('not-json', p.stdout)

    def test_ponytail_uses_native_injection(self):
        with tempfile.TemporaryDirectory() as state:
            result = hooks.ponytail({'conversationId': 'test-session', 'lastUserInput': ''}, state)
            text = result['injectSteps'][0]['systemMessage']['systemMessage']
            self.assertIn('PONYTAIL MODE ACTIVE', text)
            repeated = hooks.ponytail({'conversationId': 'test-session', 'lastUserInput': '',
                                      'hookEventName': 'PreInvocation'}, state)
            self.assertEqual(repeated, {'injectSteps': []})
            off = hooks.ponytail({'conversationId': 'test-session', 'lastUserInput': 'stop ponytail'}, state)
            self.assertIn('OFF', off['injectSteps'][0]['systemMessage']['systemMessage'])


if __name__ == '__main__':
    unittest.main()
