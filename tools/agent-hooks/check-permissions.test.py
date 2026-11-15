#!/usr/bin/env python3
"""Evidence-based check routing and permission result contracts."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('permissions', Path(__file__).with_name('check-permissions.py'))
permissions = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(permissions)


class PermissionTests(unittest.TestCase):
    def test_observed_commands_require_claude(self):
        for command, reason in [('ego-browser skill', 'BROWSER_DENIED'),
                                ('crontab -l', 'OTHER_DENIED'),
                                ('sandbox-exec -p policy bash test.sh', 'OTHER_DENIED'),
                                ('codex app-server --stdio', 'OTHER_DENIED'),
                                ('Use codex app-server --stdio', 'OTHER_DENIED'),
                                ('Run sandbox-exec -p policy bash test.sh', 'OTHER_DENIED'),
                                ("rg 'crontab -l' .; crontab -l", 'OTHER_DENIED'),
                                ('Run these checks:\ncrontab -l\nReport results.', 'OTHER_DENIED'),
                                ('```sh\ncodex app-server\n```', 'OTHER_DENIED'),
                                ('sandbox-exec\n', 'OTHER_DENIED'),
                                ('ego-browser\n', 'BROWSER_DENIED')]:
            with self.subTest(command=command):
                result = permissions.classify(command, '.')
                self.assertEqual((result['kind'], result['blocked_reason']), ('claude', reason))
                self.assertTrue(result['blocked_command'])

    def test_generic_reads_and_missing_evidence_stay_codex(self):
        for command in ['pytest tests', 'gcloud bq curl Slack', 'ps -Aww -o command',
                        'launchctl list', 'pgrep -f worker', 'Unix socket 네트워크',
                        'Permission denied', 'gcp-account-guard: refused']:
            with self.subTest(command=command):
                self.assertEqual(permissions.classify(command, '.')['kind'], 'auto')

    def test_named_test_with_nested_ps_requires_claude_without_execution(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / 'process.test.py'
            path.write_text("import subprocess\nsubprocess.run(['/bin/ps', '-axo', 'args='])\nraise RuntimeError('must not execute')\n")
            result = permissions.classify('python3 -B process.test.py', folder)
            self.assertEqual(result['blocked_reason'], 'SIGNAL_DENIED')
            self.assertEqual(result['kind'], 'claude')
            self.assertIn(str(path), result['blocked_command'])
            self.assertEqual(permissions.classify('python3 missing.test.py', folder)['kind'], 'auto')

    def test_plain_test_and_commented_ps_do_not_require_claude(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'plain.test.py'
            path.write_text("# subprocess.run(['ps']) is historical evidence\nassert 2 + 2 == 4\n")
            self.assertEqual(permissions.classify('python3 plain.test.py', folder)['kind'], 'auto')

    def test_current_full_suite_reads_nested_processes(self):
        root = Path(__file__).resolve().parents[2]
        result = permissions.classify('bash tools/git-hooks/pre-push', root)
        self.assertEqual((result['kind'], result['blocked_reason']), ('claude', 'SIGNAL_DENIED'))

    def test_reading_sources_and_search_arguments_stay_codex(self):
        root = Path(__file__).resolve().parents[2]
        for text in ['Read tools/agent-hooks/auto-tab-close.py and review its diff. Do not execute it.',
                     "Run rg 'ego-browser' tools/agent-hooks", "Run rg 'crontab -l' .",
                     "Run rg 'python3 tools/agent-hooks/auto-tab-close.py' .",
                     'git show HEAD:tools/agent-hooks/auto-tab-close.py',
                     'git diff -- tools/ego-browser.py', 'git show HEAD:tools/ego-browser.py',
                     'Read docs: ego-browser command examples; do not execute them.',
                     'Read ego-browser skill documentation; do not execute it.',
                     'git show HEAD:docs/sandbox-exec.md',
                     'Read the crontab -l documentation. Do not execute it.',
                     'Read docs for codex app-server; no launch is required.',
                     'cat tools/agent-hooks/auto-tab-close.py']:
            with self.subTest(text=text):
                self.assertEqual(permissions.classify(text, root)['kind'], 'auto')
        self.assertEqual(permissions.classify("rg 'ego-browser' .; ego-browser skill", root)['kind'], 'claude')
        self.assertEqual(permissions.classify('Use ego-browser to check the page', root)['kind'], 'claude')

    def test_valid_blockage_keeps_all_five_reasons_and_command(self):
        for reason in permissions.REASONS:
            text = f'TASK_CHECK: unknown\nblocked_reason: {reason}\nblocked_command: /bin/ps -axo args=\nretry_target: claude\n'
            self.assertEqual(permissions.blockage(text), dict(blocked_reason=reason, blocked_command='/bin/ps -axo args=', retry_target='claude'))

    def test_quoted_incomplete_invalid_and_ambiguous_fields_are_not_results(self):
        fields = 'blocked_reason: SIGNAL_DENIED\nblocked_command: ps\nretry_target: claude'
        for text in ['```\n' + fields + '\n```', '\n'.join('> ' + x for x in fields.splitlines()),
                     '```python\n' + fields,
                     fields.replace('SIGNAL_DENIED', 'GUESS'), fields.replace('claude', 'shell'),
                     fields + '\nblocked_reason: OTHER_DENIED', 'blocked_reason: SIGNAL_DENIED']:
            with self.subTest(text=text):
                self.assertEqual(permissions.blockage(text), {})


if __name__ == '__main__':
    unittest.main()
