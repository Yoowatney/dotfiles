import importlib.util
import json
import os
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
    def test_edit_deny_covers_every_native_write_tool(self):
        for folder, name in (('.ssh', 'authorized_keys'), ('.aws', 'credentials'),
                             ('.gnupg', 'gpg.conf'), ('.config/gh', 'hosts.yml')):
            target = str(Path.home() / folder / name)
            rules = ['Edit(' + str(Path.home() / folder) + '/**)']
            for native in (event('write_file', Path=target, Content='synthetic'),
                           event('write_to_file', TargetFile=target, CodeContent='synthetic'),
                           event('multi_replace_file_content', TargetFile=target, ReplacementChunks=[])):
                with self.subTest(target=target, tool=native['toolCall']['name']):
                    self.assertIsNotNone(hooks.policy_problem(hooks.translate(native), rules))
                    self.assertIsNotNone(hooks.policy_problem(hooks.translate(native), []))

        # Edit semantics also apply to paths not covered by authentication policy.
        for native in (event('write_file', Path='/tmp/locked/example.txt', Content='synthetic'),
                       event('write_to_file', TargetFile='/tmp/locked/example.txt', CodeContent='synthetic'),
                       event('multi_replace_file_content', TargetFile='/tmp/locked/example.txt', ReplacementChunks=[])):
            self.assertIsNotNone(hooks.policy_problem(hooks.translate(native), ['Edit(/tmp/locked/**)']))
            self.assertIsNotNone(hooks.policy_problem(hooks.translate(native), ['Edit']))

    def test_protected_paths_are_case_insensitive(self):
        for target in ('~/.gemini/OAuth_creds.json', '~/.CONFIG/gcloud/credentials.db',
                       '~/.local/state/antigravity-worker/GCP-AUTH/access-token'):
            for native in (event('view_file', AbsolutePath=target),
                           event('edit_file', Path=target, Target='a', Replacement='b'),
                           event('write_file', Path=target, Content='synthetic')):
                with self.subTest(target=target, tool=native['toolCall']['name']):
                    self.assertIsNotNone(hooks.policy_problem(hooks.translate(native), []))
        for target in ('~/.GEMINI', '~/.CONFIG/gcloud'):
            self.assertIsNotNone(hooks.policy_problem(hooks.translate(
                event('grep_search', SearchPath=target, Query='synthetic')), []))
        for cmd in ('cat ~/.gemini/OAUTH_C*',
                    'cat ~/.local/state/antigravity-worker/GCP-A*/access-token'):
            self.assertIsNotNone(hooks.policy_problem(hooks.translate(event(CommandLine=cmd)), []))
        self.assertIsNotNone(hooks.policy_problem(hooks.translate(event(
            'write_file', Path='~/.SSH/authorized_keys', Content='synthetic')),
            ['Edit(' + str(Path.home()) + '/.ssh/**)']))

    def test_shell_tracks_home_relative_paths_and_recursive_defaults(self):
        cases = [(cmd, '/tmp') for cmd in (
            'grep -r x $HOME', 'grep -r x "${HOME}"', 'rg -uuu x $HOME',
            'cd ~; cat .gemini/oa*', 'cd; grep -r x .gemini',
            'cd; tar cf /tmp/x.tgz .config',
            'cd ~/.local/state/antigravity-worker/GCP-AUTH; cat access-token',
            'cd ~;cat .gemini/OAUTH_C*', 'cd "$HOME" && cat .gemini/oa*')]
        cases += [('grep -r x .', str(Path.home())), ('rg -uuu x', str(Path.home())),
                  ('grep -r x ..', str(Path.home() / 'work'))]
        for cmd, cwd in cases:
            with self.subTest(command=cmd, cwd=cwd):
                self.assertIsNotNone(hooks.policy_problem(hooks.translate(event(CommandLine=cmd, Cwd=cwd)), []))

    def test_shell_allows_nonrecursive_home_listing_and_literal_slashes(self):
        for cmd in ('echo a / b', 'ls ~', "sed 's/ //' /tmp/input.txt", 'pwd',
                    'rg -uuu x /tmp/synthetic-repo', 'cd /tmp; rg x .',
                    'git -C /tmp/repository status', 'printf "%s" /'):
            with self.subTest(command=cmd):
                self.assertIsNone(hooks.policy_problem(hooks.translate(event(CommandLine=cmd)), []))

    def test_current_file_tools_use_shared_write_and_edit_guards(self):
        cases = [
            (event('write_file', Path='/tmp/agy-fixture.txt', Content='before', Overwrite=True),
             'Write', {'file_path': '/tmp/agy-fixture.txt', 'content': 'before'}),
            (event('write_file', Path='/tmp/agy-fixture.txt', Content='', Append=True),
             'Write', {'file_path': '/tmp/agy-fixture.txt', 'content': ''}),
            (event('edit_file', Path='/tmp/agy-fixture.txt', Target='before', Replacement='after'),
             'Edit', {'file_path': '/tmp/agy-fixture.txt', 'old_string': 'before', 'new_string': 'after'}),
        ]
        for native, name, inputs in cases:
            with self.subTest(name=name, inputs=inputs):
                with patch.object(hooks, 'settings', return_value=([], [{'hooks': [
                        {'type': 'command', 'command': 'shared-guard'}]}])), \
                     patch.object(hooks, 'run_guard', return_value=None) as guard:
                    self.assertEqual(hooks.pre_tool(native)['decision'], 'allow')
                    payload = guard.call_args.args[1]
                    self.assertEqual(payload['tool_name'], name)
                    self.assertEqual(payload['tool_input'], inputs)

    def test_current_file_tools_preserve_denials_and_reject_bad_payloads(self):
        for native in [event('write_file', Path='/tmp/agy-fixture.txt', Content='test'),
                       event('edit_file', Path='/tmp/agy-fixture.txt', Target='a', Replacement='b')]:
            with self.subTest(native=native), patch.object(hooks, 'settings', return_value=(
                    [f"{tool}({Path('/tmp/agy-fixture.txt').resolve()})" for tool in ('Write', 'Edit')], [])):
                self.assertEqual(hooks.pre_tool(native)['decision'], 'deny')
        protected = str(Path.home() / '.gemini/oauth_creds.json')
        for native in [event('write_file', Path=protected, Content='test'),
                       event('edit_file', Path=protected, Target='a', Replacement='b'),
                       event('write_file', Path='/tmp/test', Content=None),
                       event('edit_file', Path='/tmp/test', Target='a', Replacement=None),
                       event('edit_file', Path='/tmp/test', Target=None, Replacement='a')]:
            with self.subTest(native=native):
                self.assertEqual(hooks.pre_tool(native)['decision'], 'deny')

    def test_gcloud_exposes_only_a_cached_agent_access_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            folder = home / '.config/gcloud'
            (folder / 'configurations').mkdir(parents=True)
            (folder / 'active_config').write_text('munice')
            principal = 'ai-agent-test@example.iam.gserviceaccount.com'
            (folder / 'configurations/config_munice').write_text('[auth]\nimpersonate_service_account = ' + principal)
            adc = folder / 'application_default_credentials.json'
            data = {'type': 'impersonated_service_account', 'service_account_impersonation_url':
                    'https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/' + principal + ':generateAccessToken'}
            adc.write_text(json.dumps(data))
            with patch.object(hooks, 'HOME', home), patch.object(hooks.subprocess, 'run') as run:
                run.return_value.returncode = 0
                run.return_value.stdout = 'synthetic-agent-token\n'
                env = hooks.gcloud_environment()
                self.assertEqual(env, ['CLOUDSDK_CONFIG=' + str(home / '.local/state/antigravity-worker/gcloud'),
                                      'CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE=',
                                      'CLOUDSDK_AUTH_ACCESS_TOKEN_FILE=' + str(home / '.local/state/antigravity-worker/gcp-auth/access-token'),
                                      'CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT='])
                self.assertIn('--impersonate-service-account=' + principal, run.call_args.args[0])
                token = home / '.local/state/antigravity-worker/gcp-auth/access-token'
                self.assertEqual(token.read_text(), 'synthetic-agent-token')
                self.assertEqual(token.stat().st_mode & 0o777, 0o400)
                self.assertNotIn(str(adc), ' '.join(env))
                hooks.gcloud_environment()
                self.assertEqual(run.call_count, 1)
                data['service_account_impersonation_url'] += 'wrong'
                adc.write_text(json.dumps(data))
                with self.assertRaises(ValueError):
                    hooks.gcloud_environment()

    def test_gcloud_token_fetch_error_fails_closed_without_error_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            folder = home / '.config/gcloud'
            (folder / 'configurations').mkdir(parents=True)
            (folder / 'active_config').write_text('munice')
            principal = 'ai-agent-test@example.iam.gserviceaccount.com'
            (folder / 'configurations/config_munice').write_text('[auth]\nimpersonate_service_account = ' + principal)
            (folder / 'application_default_credentials.json').write_text(json.dumps({
                'type': 'impersonated_service_account', 'service_account_impersonation_url':
                'https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/' + principal + ':generateAccessToken'}))
            with patch.object(hooks, 'HOME', home), patch.object(hooks.subprocess, 'run') as run:
                run.return_value.returncode = 1
                run.return_value.stderr = 'synthetic-sensitive-error'
                with self.assertRaisesRegex(ValueError, '^agent access token refresh failed$'):
                    hooks.gcloud_environment()
                (folder / 'active_config').write_text('munice-admin')
                with self.assertRaises(ValueError):
                    hooks.gcloud_environment()

    def test_gcloud_configuration_change_is_denied_before_rewrite(self):
        for command in ('gcloud config set project example', 'gcloud config unset auth/impersonate_service_account',
                        'gcloud --project=example config set project another',
                        'gcloud --quiet config configurations activate munice',
                        'gcloud --configuration=munice-admin version'):
            with self.subTest(command=command):
                self.assertEqual(hooks.pre_tool(event(CommandLine=command))['decision'], 'deny')

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

    def test_sandbox_bypass_is_denied(self):
        with patch.object(hooks, 'settings', return_value=([], [])):
            for value in (True, 'true', 1, 'invalid', None):
                with self.subTest(value=value):
                    self.assertEqual(hooks.pre_tool(event(CommandLine='printf OK', BypassSandbox=value))['decision'], 'deny')
            for value in (False, 'false'):
                with self.subTest(value=value):
                    self.assertEqual(hooks.pre_tool(event(CommandLine='printf OK', BypassSandbox=value))['decision'], 'allow')

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

    def test_subscription_oauth_file_is_never_read_or_changed(self):
        target = str(Path.home() / '.gemini/oauth_creds.json')
        cases = [event('view_file', AbsolutePath=target),
                 event('grep_search', SearchPath=target, Query='.'),
                 event('grep_search', SearchPath=str(Path.home() / '.gemini'), Query='synthetic'),
                 event('write_to_file', TargetFile=target, CodeContent='fixture'),
                 event(CommandLine='cat ~/.gemini/oauth_creds.json'),
                 event(CommandLine='cat "${HOME}/.gemini/oauth_creds.json"')]
        for payload in cases:
            with self.subTest(payload=payload):
                self.assertEqual(hooks.pre_tool(payload)['decision'], 'deny')

    def test_agent_token_is_only_accessible_to_sdk(self):
        target = str(Path.home() / '.local/state/antigravity-worker/gcp-auth/access-token')
        cases = [event('view_file', AbsolutePath=target),
                 event('grep_search', SearchPath=str(Path.home() / '.local/state'), Query='synthetic'),
                 event('write_to_file', TargetFile=target, CodeContent='fixture'),
                 event(CommandLine='cat "$CLOUDSDK_AUTH_ACCESS_TOKEN_FILE" >/dev/null'),
                 event(CommandLine='cat ~/.local/state/antigravity-worker/gcp-auth/ac* >/dev/null')]
        for payload in cases:
            with self.subTest(payload=payload):
                self.assertEqual(hooks.pre_tool(payload)['decision'], 'deny')

    def test_source_auth_globs_and_recursive_searches_are_denied_without_sandbox(self):
        for command in ('cat ~/.gemini/oa* >/dev/null',
                        'cat ~/.config/gcloud/application_d* >/dev/null',
                        'grep -R synthetic ~/.gemini',
                        'rg synthetic "$HOME/.config/gcloud"',
                        'cat ${HOME}/.config/gcloud/credentials.db >/dev/null'):
            with self.subTest(command=command):
                self.assertEqual(hooks.pre_tool(event(CommandLine=command))['decision'], 'deny')
        for path in ('~/.config/gcloud', '~/.config/gcloud/credentials.db', '~/.gemini/oa*'):
            with self.subTest(path=path):
                self.assertEqual(hooks.pre_tool(event('view_file', AbsolutePath=path))['decision'], 'deny')

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

    def test_host_without_agent_gcp_runs_tools_with_an_empty_gcloud_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / '.config/gcloud').mkdir(parents=True)
            (home / '.config/gcloud/active_config').write_text('default')
            with patch.object(hooks, 'HOME', home), patch.object(hooks, 'settings', return_value=([], [])):
                result = hooks.pre_tool(event(CommandLine='printf OK'))
            self.assertEqual(result['decision'], 'allow')
            line = result['overwrite']['CommandLine']
            self.assertIn('CLOUDSDK_CONFIG=' + str(home / '.local/state/antigravity-worker/no-gcloud'), line)
            self.assertNotIn('access-token', line)

    def test_safe_command_is_delegated_without_auto_approval(self):
        result = hooks.pre_tool(event(CommandLine='printf OK'))
        self.assertEqual(result['decision'], 'allow')

    def test_native_shell_removes_reinjected_auth_overrides(self):
        keys = ('GEMINI_API_KEY', 'GOOGLE_API_KEY', 'AGY_ADC_AUTH', 'GOOGLE_APPLICATION_CREDENTIALS',
                'GOOGLE_CLOUD_PROJECT', 'GOOGLE_CLOUD_PROJECT_ID', 'GOOGLE_CLOUD_QUOTA_PROJECT',
                'GOOGLE_GENAI_USE_VERTEXAI')
        script = 'import os; print(sum(bool(os.environ.get(k)) for k in ' + repr(keys) + '))'
        import shlex
        command = shlex.join([sys.executable, '-c', script])
        with patch.object(hooks, 'settings', return_value=([], [])):
            result = hooks.pre_tool(event(CommandLine=command))
        self.assertEqual(result['decision'], 'allow')
        child = subprocess.run(result['overwrite']['CommandLine'], shell=True, capture_output=True,
                               text=True, env={**os.environ, **dict.fromkeys(keys, 'fixture')})
        self.assertEqual(child.returncode, 0)
        self.assertEqual(child.stdout.strip(), '0')

    def test_sandbox_control_bridge_only_reaches_own_lead(self):
        env = {'HERDR_AGY_STARTUP_ROLE': 'worker', 'HERDR_AGY_LEAD': 'default/wC:p1'}
        with patch.object(hooks.subprocess, 'run') as run:
            run.return_value.returncode = 0
            result = hooks.control_command('PANE_TELL_BATCH=0 pane-tell default/wC:p1 "Progress update"', env)
            self.assertEqual(result['decision'], 'allow')
            self.assertIn('CommandLine', result['overwrite'])
            self.assertEqual(run.call_args.args[0][-2:], ['default/wC:p1', 'Progress update'])
            self.assertFalse(run.call_args.kwargs.get('shell', False))
            self.assertEqual(run.call_args.kwargs['env']['PANE_TELL_BATCH'], '0')
            for cmd in ['pane-tell default/wC:p9 hi', 'pane-label --close',
                        'pane-label --free; touch /tmp/unexpected', 'pane-tell --raw default/wC:p1 hi']:
                with self.subTest(cmd=cmd), self.assertRaises(ValueError):
                    hooks.control_command(cmd, env)
            self.assertIsNone(hooks.control_command('printf OK', env))

    def test_control_bridge_does_not_grant_nonworkers_host_execution(self):
        with self.assertRaises(ValueError):
            hooks.control_command('pane-label --free', {})

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
