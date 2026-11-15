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

    def test_unresolved_process_launches_require_claude_without_execution(self):
        sources = [
            "import subprocess\ncommand = ['codex', 'app-server']\nsubprocess.Popen(command)\n",
            "import subprocess as sp\nsp.run(command)\n",
            "from subprocess import Popen as launch\nlaunch(args=command)\n",
            "import subprocess\nsubprocess.check_output(*commands)\n",
            "import subprocess\nsubprocess.run(['echo'], **options)\n",
            "import subprocess\nsubprocess.run(['echo'], executable=program)\n",
            "import os\nos.execv(program, arguments)\n",
            "from os import execvp as replace\nreplace('python3', arguments)\n",
            "import os\nos.system(command)\n",
            "import asyncio\nasyncio.create_subprocess_exec(*command)\n",
            "exec(source)\n",
            "import os\nos.execv('/bin/sh', ['sh', '-c', '$COMMAND'])",
            "import os\nos.execl('/bin/sh', 'sh', '-c', '$COMMAND')",
            "import os\nos.spawnv(0, '/bin/sh', ['sh', '-c', '$COMMAND'])",
            "import os\nos.posix_spawn('/bin/sh', ['sh', '-c', '$COMMAND'], {})",
        ]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'codex-fast-probe.py'
            for source in sources:
                with self.subTest(source=source):
                    path.write_text(source + "\nraise RuntimeError('must not execute')\n")
                    result = permissions.classify('python3 -B codex-fast-probe.py', folder)
                    self.assertEqual(result['kind'], 'claude')
                    self.assertIn('unresolved', result['reason'])
                    self.assertIn(str(path), result['blocked_command'])

    def test_literal_process_commands_keep_existing_routing(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'literal.py'
            for source, kind in [
                    ("import subprocess\nsubprocess.run(['echo', 'ok'])", 'auto'),
                    ("import subprocess\nsubprocess.run(['echo', 'ok; ps -A'])", 'auto'),
                    ("import subprocess as sp\nsp.run(['codex', 'app-server'])", 'claude'),
                    ("from subprocess import run\nrun(args=['ps', '-A'])", 'claude'),
                    ("import os\nos.execv('/bin/echo', ['echo', 'ok'])", 'auto'),
                    ("import os\nos.execv('/bin/ps', ['ps', '-A'])", 'claude'),
                    ("import os\nos.execvp('codex', ['codex', 'app-server'])", 'claude')]:
                with self.subTest(source=source):
                    path.write_text(source)
                    self.assertEqual(permissions.classify('python3 literal.py', folder)['kind'], kind)

    def test_process_aliases_and_shell_wrappers_do_not_hide_launches(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'wrapper.py'
            for source in [
                    "import subprocess as sp\nlaunch = sp.Popen\nlaunch(command)",
                    "from builtins import exec as execute\nexecute(source)",
                    "import subprocess\nsubprocess.run(['sh', '-c', '$COMMAND'])",
                    "import subprocess\nsubprocess.run(['ignored', '-c', '$COMMAND'], executable='/bin/bash')",
                    "import subprocess\nsubprocess.run(['bash', '-c', 'ps -A'])",
                    "import subprocess\nsubprocess.run(['bash', '-c', 'ps'])",
                    "import subprocess\nsubprocess.run(['bash', '-c', 'exec ps -A'])",
                    "import subprocess\nsubprocess.run(['bash', '--norc', '-c', '$COMMAND'])",
                    "import subprocess\nsubprocess.run(['bash', '--noprofile', '--norc', '-c', 'ps -A'])",
                    "import os\nos.system('echo ok; ps -A')",
                    "import subprocess\nsubprocess.getoutput('echo ok; ps -A')",
                    "import subprocess\nsubprocess.run('echo ok; ps -A', shell=True)",
                    "import subprocess\nsubprocess.run(['echo ok; ps -A'], shell=True)",
                    "import asyncio\nasyncio.create_subprocess_shell('echo ok; ps -A')",
                    "import subprocess\nsubprocess.run(['bash', '-c', 'codex app-server'])"]:
                with self.subTest(source=source):
                    path.write_text(source)
                    self.assertEqual(permissions.classify('python3 wrapper.py', folder)['kind'], 'claude')
            path = Path(folder) / 'wrapper.sh'
            for source in ['exec "$command"', '"$command"', 'eval "$command"', 'bash -c "$command"',
                           'command "$COMMAND"', 'bash -lc "$COMMAND"', 'exec "$@"',
                           'ps', 'exec ps -A', 'command ps', "'ps' -A"]:
                with self.subTest(source=source):
                    path.write_text(source)
                    self.assertEqual(permissions.classify('bash wrapper.sh', folder)['kind'], 'claude')
            for source in ["printf '%s' '$COMMAND'\necho 'ps -A'\n", "echo 'bash -c $COMMAND'",
                           "grep 'bash -c $COMMAND' README.md", "printf '%s' 'example; ps -A'"]:
                with self.subTest(source=source):
                    path.write_text(source)
                    self.assertEqual(permissions.classify('bash wrapper.sh', folder)['kind'], 'auto')

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
