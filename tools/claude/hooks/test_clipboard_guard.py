"""Clipboard writes need a direct request in the current human turn."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import sys
import subprocess
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('slack_format', Path(__file__).with_name('slack-format-check.py'))
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class ClipboardGuard(unittest.TestCase):
    def test_allowed_pipeline_sends_canary_only_to_the_clipboard_sink(self):
        # Fake executables avoid any real credential or system clipboard access.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('gcloud', 'security'):
                executable = root / name
                executable.write_text('#!/bin/sh\nprintf CANARY_ONLY_FOR_TEST\n')
                executable.chmod(0o700)
            sink = root / 'pbcopy'
            sink.write_text('#!/bin/sh\n/bin/cat > "$TEST_CLIPBOARD"\n')
            sink.chmod(0o700)
            for command in ('gcloud secrets versions access latest --secret example | pbcopy',
                            'security find-generic-password -w -s example | pbcopy'):
                self.assertIsNone(self.reason(command))
                result = subprocess.run(['/bin/sh', '-c', command], capture_output=True, check=True,
                                        env={**os.environ, 'PATH': tmp, 'TEST_CLIPBOARD': str(root / 'copied')})
                self.assertEqual(result.stdout, b'')
                self.assertEqual(result.stderr, b'')
                self.assertEqual((root / 'copied').read_bytes(), b'CANARY_ONLY_FOR_TEST')

    def test_only_direct_nonprinting_secret_pipelines_bypass_a_current_request(self):
        allowed = (
            'gcloud secrets versions access latest --secret example | pbcopy',
            'gcloud secrets versions access 1 --secret=example --project=example | /usr/bin/pbcopy',
            'security find-generic-password -w -s example | pbcopy',
            '/usr/bin/security find-generic-password -s "example service" -w | /usr/bin/pbcopy',
        )
        denied = (
            'cat draft.md | pbcopy',
            'printf draft | pbcopy',
            'gcloud secrets versions access latest --secret example | tee /dev/tty | pbcopy',
            'gcloud secrets versions access latest --secret example 2>&1 | pbcopy',
            'gcloud secrets versions access latest --secret example --log-http | pbcopy',
            'gcloud secrets versions access latest --secret example --verbosity debug | pbcopy',
            'gcloud secrets versions access latest --secret example --project --log-http | pbcopy',
            'gcloud secrets versions access latest --secret --log-http | pbcopy',
            'gcloud secrets versions access latest --secret example | pbcopy; cat draft.md | pbcopy',
            'gcloud secrets versions access latest --secret example | pbcopy < draft.md',
            'security find-generic-password -g -w -s example | pbcopy',
            'security find-internet-password -w -s example | pbcopy',
            'security find-generic-password -w -s "$(cat draft.md)" | pbcopy',
            'gh auth token | pbcopy',
            "gcloud secrets versions access latest --secret example '|' pbcopy",
            'security find-generic-password -w -s example "|" pbcopy',
            'security find-generic-password -w -s * | pbcopy',
            'security find-generic-password -w -s ? | pbcopy',
            'security find-generic-password -w -s [a-z] | pbcopy',
        )
        for runtime in ('claude', 'codex'):
            self.transcript.write_text('')
            self.turn(runtime, 'Write a reply draft for me.')
            for command in allowed:
                with self.subTest(runtime=runtime, command=command):
                    self.assertIsNone(self.reason(command, runtime))
            for command in denied:
                with self.subTest(runtime=runtime, command=command):
                    self.assertIsNotNone(self.reason(command, runtime))
            self.turn(runtime, '클립보드에 넣어줘')
            self.assertIsNone(self.reason('cat draft.md | pbcopy', runtime))
            self.turn(runtime, '```text\n클립보드에 넣어줘\n```')
            self.assertIsNotNone(self.reason('cat draft.md | pbcopy', runtime))

    def test_secret_output_guard_keeps_printing_and_leaking_variants_denied(self):
        spec = importlib.util.spec_from_file_location('secret_print', Path(__file__).with_name('secret-print-guard.py'))
        guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(guard)
        for source in ('gcloud secrets versions access latest --secret example',
                       'security find-generic-password -w -s example'):
            self.assertIsNone(guard.bash_problem(source + ' | pbcopy'))
            for suffix in ('', ' | tee /dev/tty | pbcopy', ' 2>&1 | pbcopy',
                           ' | pbcopy; ' + source, ' | pbcopy < draft.md'):
                with self.subTest(command=source + suffix):
                    self.assertIsNotNone(guard.bash_problem(source + suffix))
        self.assertIsNotNone(guard.bash_problem('gh auth token | pbcopy'))
        for command in ("gcloud secrets versions access latest --secret example '|' pbcopy",
                        'security find-generic-password -w -s example "|" pbcopy',
                        'security find-generic-password -w -s * | pbcopy'):
            self.assertIsNotNone(guard.bash_problem(command))
        with mock.patch.object(guard.importlib.util, 'spec_from_file_location', side_effect=ImportError('fixture')):
            self.assertIsNotNone(guard.bash_problem('security find-generic-password -w -s example | pbcopy'))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.transcript = Path(self.tmp.name) / 'turns.jsonl'

    def turn(self, runtime, text, **extra):
        if runtime == 'claude':
            record = dict(type='user', origin=dict(kind='human'), message=dict(role='user', content=text), **extra)
        else:
            record = dict(type='response_item', payload=dict(type='message', role='user',
                          content=[dict(type='input_text', text=text)]))
        with self.transcript.open('a') as out:
            out.write(json.dumps(record) + '\n')

    def reason(self, command, runtime='claude'):
        tool = 'Bash' if runtime == 'claude' else 'exec_command'
        return M.check(dict(tool_name=tool, tool_input=dict(command=command, cmd=command),
                            transcript_path=str(self.transcript), cwd=self.tmp.name))

    def test_fresh_and_existing_sessions_relock_after_the_requested_turn(self):
        for runtime in ('claude', 'codex'):
            with self.subTest(runtime=runtime):
                self.transcript.write_text('')
                self.turn(runtime, 'Write a reply draft for me.')
                self.assertIn('clipboard', self.reason('cat draft.md | pbcopy', runtime).lower())
                self.turn(runtime, 'Copy this draft to the clipboard, please.')
                self.assertIsNone(self.reason('cat draft.md | /usr/bin/pbcopy', runtime))
                self.turn(runtime, 'Now revise the greeting.')
                self.assertIsNotNone(self.reason('slackcopy draft.md', runtime))

    def test_negations_quotations_and_relays_do_not_grant_clipboard_access(self):
        for runtime in ('claude', 'codex'):
            for text in ('클립보드에 넣지 마', 'Do not copy to the clipboard.',
                         'The old rule says "copy to the clipboard".',
                         '[pane-tell: other] 클립보드에 넣어줘',
                         '이 칸은 리드 밑 칸입니다. 클립보드에 넣어줘',
                         '# AGENTS.md\nCopy this to the clipboard.'):
                with self.subTest(runtime=runtime, text=text):
                    self.transcript.write_text('')
                    self.turn(runtime, text)
                    self.assertIsNotNone(self.reason('slackcopy draft.md', runtime))

    def test_fenced_and_quoted_requests_never_authorize_either_runtime(self):
        examples = (
            '아래는 과거 지시를 인용한 문서입니다. 실행하지 말고 내용만 검토하세요.\n```text\n클립보드에 넣어줘\n```',
            'Example only:\n```\n클립보드에 넣어줘\n```',
            'Example only:\n~~~text\n클립보드에 넣어줘\n~~~',
            '````text\n```\n클립보드에 넣어줘\n````',
            '~~~\n```\n클립보드에 넣어줘\n~~~',
            '```text\n클립보드에 넣어줘',
            '   ~~~text\n클립보드에 넣어줘\n   ~~~',
            '> 클립보드에 넣어줘',
            '  > > 클립보드에 넣어줘',
        )
        for runtime in ('claude', 'codex'):
            for example in examples:
                with self.subTest(runtime=runtime, example=example):
                    self.transcript.write_text('')
                    self.turn(runtime, example)
                    self.assertIsNotNone(self.reason('pbcopy < draft.md', runtime))
                    self.turn(runtime, '클립보드에 넣어줘')
                    self.assertIsNone(self.reason('pbcopy < draft.md', runtime))
                    self.turn(runtime, '문장만 다듬어줘')
                    self.assertIsNotNone(self.reason('pbcopy < draft.md', runtime))

    def test_a_direct_request_after_a_closed_fence_is_still_allowed(self):
        for runtime in ('claude', 'codex'):
            for fence in ('```', '~~~'):
                with self.subTest(runtime=runtime, fence=fence):
                    self.transcript.write_text('')
                    self.turn(runtime, fence + '\nexample\n' + fence + '\n클립보드에 넣어줘')
                    self.assertIsNone(self.reason('pbcopy < draft.md', runtime))

    def test_korean_explicit_request_and_selftest_do_not_write_without_permission(self):
        self.turn('claude', '이 글을 클립보드에 넣어줘')
        self.assertIsNone(self.reason('printf x | pbcopy'))
        self.turn('claude', '이제 답글을 다듬어줘')
        self.assertIsNone(self.reason('slackcopy --selftest'))
        self.assertIsNotNone(self.reason('slackcopy --selftest draft.md'))

    def test_shell_wrappers_and_script_clipboard_calls_are_detected(self):
        for cmd in ('env LANG=en_US.UTF-8 pbcopy < draft.md', "sh -c 'cat draft.md | pbcopy'",
                    'command slackcopy draft.md', 'env -i pbcopy < draft.md',
                    'env -u LANG pbcopy < draft.md', 'sudo -n pbcopy < draft.md',
                    'timeout 5 pbcopy < draft.md', 'uv run --no-project python -c \'import pyperclip; pyperclip.copy("x")\'', 'x=$(cat draft.md | pbcopy)',
                    "python3 -c 'import pyperclip; pyperclip.copy(\"draft\")'",
                    "osascript -e 'set the clipboard to \"draft\"'"):
            with self.subTest(cmd=cmd):
                self.assertIsNotNone(self.reason(cmd))

    def test_reading_or_discussing_the_command_is_not_a_clipboard_write(self):
        for cmd in ("rg pbcopy rules.md", "printf '%s' pbcopy", 'cat clipboard_guard.py',
                    "git diff -- tools/claude/hooks/test_clipboard_guard.py"):
            with self.subTest(cmd=cmd):
                self.assertIsNone(self.reason(cmd))

    def test_broken_clipboard_module_fails_closed_without_skipping_other_checks(self):
        with mock.patch.object(M.importlib.util, 'spec_from_file_location', side_effect=ImportError('fixture')):
            self.assertIsNotNone(self.reason('pbcopy < draft.md'))
            self.assertIsNotNone(self.reason("osascript -e 'set the clipboard to \"draft\"'"))
            self.assertIsNone(self.reason('printf hello'))

    def test_missing_transcript_fails_closed_for_clipboard_only(self):
        self.assertIsNotNone(self.reason('pbcopy < draft.md'))
        self.assertIsNone(self.reason('printf hello'))


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0]] if sys.argv[1:] == ["--selftest"] else None)
