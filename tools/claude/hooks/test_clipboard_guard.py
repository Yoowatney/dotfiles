"""Clipboard writes need a direct request in the current human turn."""
import importlib.util
import json
from pathlib import Path
import tempfile
import sys
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('slack_format', Path(__file__).with_name('slack-format-check.py'))
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class ClipboardGuard(unittest.TestCase):
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
