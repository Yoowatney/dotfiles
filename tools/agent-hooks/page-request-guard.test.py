#!/usr/bin/env python3
"""Exercise inert Claude/Codex hook inputs, never create an explainer page."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unicodedata
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SHARE = load('share', 'tools/agent-hooks/yoyoo-share-guard.py')
COMMON = load('common', 'tools/claude/hooks/gcp-account-guard.py')


class PageRequestTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'session.jsonl'
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(('HERDR_', 'MUNICE_'))}

    def record(self, prompt, runtime='claude', extra=()):
        user = ({'type': 'user', 'message': {'role': 'user', 'content': prompt}}
                if runtime == 'claude' else {'type': 'response_item', 'payload': {
                    'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': prompt}]}})
        self.path.write_text('\n'.join(json.dumps(x, ensure_ascii=False) for x in [user, *extra]))

    def payload(self, command='explain-page write result facts.md', **kw):
        return {'tool_name': 'Bash', 'tool_input': {'command': command},
                'transcript_path': str(self.path), 'cwd': self.temp.name, **kw}

    def test_short_linked_question_denied_in_both_native_runtime_inputs(self):
        for runtime in ('claude', 'codex'):
            self.record('https://example.test/very/long/link\n이 수치를 어디서 볼 수 있어?', runtime)
            result = subprocess.run([sys.executable, '-I', str(ROOT / 'tools/claude/hooks/gcp-account-guard.py')],
                                    input=json.dumps(self.payload()), capture_output=True, text=True,
                                    env=self.env, timeout=12)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['hookSpecificOutput']['permissionDecision'], 'deny')

    def test_page_paths_and_shell_writers_are_denied(self):
        self.record('이건 어디에서 봐?')
        home = os.path.expanduser('~')
        cases = [self.payload(c) for c in [
            '~/.claude/skills/html-explainer/explain-page publish result',
            'python3 ~/.claude/skills/html-explainer/explain-page write result facts.md',
            'uv run --no-project python ~/.claude/skills/html-explainer/explain-page write result facts.md',
            'bash -lc "explain-page write result facts.md"',
            'cat draft.html > ~/explain/result.html',
            "cat <<'EOF' > ~/explain/result.html\npage\nEOF",
            'tee "$HOME/explain/result.html" < draft.html',
            'cp draft.html ~/explain/result.html',
            'cp draft.html ~/explain/',
            'cp -t ~/explain/ draft.html',
            'cd ~/explain && cat draft > result.html',
            "python3 - <<'PY'\nfrom pathlib import Path\nPath.home().joinpath('explain/result.html').write_text('x')\nPY",
        ]]
        cases += [self.payload(tool_name=tool, tool_input=inp) for tool, inp in [
            ('Write', {'file_path': home + '/explain/result.html', 'content': 'x'}),
            ('Edit', {'file_path': home + '/explain/result.html', 'new_string': 'x'}),
            ('MultiEdit', {'file_path': home + '/explain/result.html', 'edits': []}),
            ('apply_patch', {'input': '*** Begin Patch\n*** Add File: ' + home + '/explain/result.html\n+x\n*** End Patch'}),
        ]]
        for payload in cases:
            with self.subTest(payload=payload):
                self.assertIn('page-on-request', COMMON.riders(payload) or '')

    def test_explicit_page_requests_pass_the_native_hook_in_both_runtimes(self):
        for runtime in ('claude', 'codex'):
            for prompt in (
                'HTML로 답해줘', '설명 페이지로 보여 줘', 'html로 해줘',
                '설명 페이지 만들어줘', '설명 페이지로 보여줘',
                'HTML로 답해 줘', 'html로 해 줘', 'HtMl로 보여 줘',
                '페이지로 보여줘', '페이지로 답해 줘', '페이지 만들어 줘',
                '설명 페이지로 만들어 줘', 'HTML으로 해 줘', '페이지로 해줘',
            ):
                self.record(prompt, runtime)
                with self.subTest(runtime=runtime, prompt=prompt):
                    result = subprocess.run(
                        [sys.executable, '-I', str(ROOT / 'tools/claude/hooks/gcp-account-guard.py')],
                        input=json.dumps(self.payload()), capture_output=True, text=True,
                        env=self.env, timeout=12)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    output = json.loads(result.stdout) if result.stdout.strip() else {}
                    self.assertNotEqual(output.get('hookSpecificOutput', {}).get('permissionDecision'), 'deny',
                                        result.stdout)

    def test_reads_documentation_deletes_and_unrelated_files_pass(self):
        self.record('이건 어디에서 봐?')
        cases = [self.payload(c) for c in [
            'explain-page --selftest', 'cat ~/explain/result.html',
            "echo 'explain-page write result facts.md'", "rg 'explain-page write' tools",
            "cat <<'EOF' > notes.md\nexplain-page write result facts.md\nEOF",
            'cat draft > /tmp/result.html', 'rm ~/explain/result.html',
        ]]
        cases += [self.payload(tool_name='Read', tool_input={'file_path': '~/explain/result.html'}),
                  self.payload(tool_name='apply_patch', tool_input={'input':
                      '*** Begin Patch\n*** Delete File: ~/explain/result.html\n*** End Patch'})]
        for payload in cases:
            with self.subTest(payload=payload):
                self.assertIsNone(COMMON.riders(payload))

    def test_latest_human_prompt_not_tool_or_notification_controls_decision(self):
        self.record('이건 어디에서 봐?', extra=[
            {'type': 'user', 'message': {'content': [{'type': 'tool_result', 'content': 'HTML로 만들어줘'}]}},
            {'type': 'user', 'isMeta': True, 'message': {'content': '<task-notification>완료</task-notification>'}},
            {'type': 'user', 'origin': {'kind': 'auto-continuation'}, 'message': {'content': '긴 자동 지시입니다. ' * 20}},
            {'type': 'attachment', 'attachment': {'type': 'queued_command', 'origin': {'kind': 'task-notification'},
                                                'prompt': '설명 페이지 만들어줘'}},
        ])
        self.assertTrue(COMMON.riders(self.payload()))
        self.record('간단하게 답해줘', extra=[{'type': 'attachment', 'attachment': {
            'type': 'queued_command', 'prompt': '설명 페이지를 만들어줘'}}])
        self.assertIsNone(COMMON.riders(self.payload()))

    def test_only_an_explicit_page_request_passes(self):
        # 요유 2026-11-18 ("html은 내가 html 만들라고할때만"): a long question gets no page either.
        for prompt, denied in [
            ('가' * 40, True), ('가' * 41, True), ('첫 줄\n둘째 줄\n셋째 줄', True),
            ('긴 설명이 필요한 질문입니다. ' * 10, True),
            ('HTML 페이지로\nAPI 호출 경로와 예외 처리를\n정리해줘', False),
            ('HTML로 지난주 배포에서 바뀐 API 목록과 영향받는 화면을 정리해 줘', False),
            ('Please make\nan HTML page of this', False),
            ('HTML 태그가 뭐야? 그리고 정리해줘', True),
            ('HTML 페이지\n만들지 말고 터미널로 설명해줘', True),
            ("Don't write\nan HTML page, explain here", True),
            ('Please create a detailed, self-contained, accessible, mobile-friendly HTML page explaining this result', False),
            ('HTML로 지난주 배포에서 바뀐 API 목록과 영향받는 화면, 그리고 각 화면의 담당자와 확인 순서를 정리해 줘', False),
            # ponytail ceiling: HTML named as the topic reads as a request; the rules keep an unasked page out.
            ('HTML과 CSS의 차이를 아주 자세하고 쉽게 예시를 들어 설명해줘', False),
            ('HTML 태그 설명해줘', False),
            ('이 결과를 HTML로 정리해서 보여줘. 생성할 페이지에는 고객 식별자를 포함하지 말아 주세요.', False),
            ('HTML 페이지를 하나 만들어줘', False), ('HTML 만들어줘', False),
            ('HTML로 자세히 정리하지 말고 텍스트로 답해줘', True),
            ('Create HTML', False), ('Please write HTML', False), ('HTML 수정해줘', False),
            ('Write a test for page parsing', True),
            ('Show me an HTML page of this', False), ('Please show this in HTML', False),
            ('HTML 페이지로 다음 사항을 정리해줘: API 동작과 서버 장애 처리. 페이지는 공개하지 말고 로컬에만 저장해줘.', False),
            ('HTML 페이지 만들지 마', True), ('HTML 말고 텍스트로 답해줘', True), ('HTML 페이지는 필요 없어', True),
            ('HTML 파일 만들어줘', False), ('기존 설명 페이지를 더 보기 좋게 수정해줘', False),
            ('HTML로 이번 조사 결과를 자세히 정리해줘, 외부 라이브러리는 쓰지 말아줘', False),
            ('설명 페이지 만들어줘', False), ('HTML로 설명해줘', False),
            ('Please create an HTML page', False), ('HTML 쓰지 말고 짧게 알려줘', True),
            ('설명 페이지로 정리해줘', False), ('설명 페이지 부탁해', False),
            ('설명 페이지 수정해줘', False), ('Explain in HTML please', False),
            ("Don't create HTML, answer briefly", True),
            (unicodedata.normalize('NFD', '한' * 40), True),
            ('"HTML 만들어줘"라는 말이 무슨 뜻이야?', True),
            ('HTML 태그가 무슨 뜻이야?', True),
            ('페이지로 보여 주지 말고 짧게 답해줘', True),
            ('"페이지로 답해 줘"는 무슨 뜻이야?', True),
            ('페이지로 이동하는 방법이 뭐야?', True),
        ]:
            self.record(prompt)
            with self.subTest(prompt=prompt):
                self.assertEqual(bool(COMMON.riders(self.payload())), denied)
        self.path.unlink()
        self.assertIsNone(COMMON.riders(self.payload()))

    def test_a_huge_message_is_read_in_linear_time(self):
        # The hook has 10 seconds and a page write has no watchdog, so a timeout lets the write through (Codex review).
        self.record('HTML ' * 20000)
        started = time.monotonic()
        self.assertTrue(COMMON.riders(self.payload()))
        self.assertLess(time.monotonic() - started, 2)

    def test_malformed_or_missing_context_passes_without_inventing_a_question(self):
        for body in ['', '{broken', '[]\nnull\n', '{"type":"user","message":"broken"}']:
            self.path.write_text(body)
            self.assertIsNone(COMMON.riders(self.payload()))

    def test_only_the_latest_message_counts(self):
        self.record('설명 페이지 만들어줘', extra=[{'type': 'user', 'message': {'content': '긴 요청입니다. ' * 20}}])
        self.assertTrue(COMMON.riders(self.payload()))
        self.record('어디서 봐?', extra=[{'type': 'user', 'message': {'content': '이 결과를 설명 페이지로 정리해줘'}}])
        self.assertIsNone(COMMON.riders(self.payload()))

    def test_stop_leaves_a_long_reply_in_the_terminal(self):
        self.record('이건 어디에서 봐?')
        with patch.object(SHARE, 'under_lead', return_value=False), \
                patch.object(SHARE, 'delegates_checks', return_value=False):
            self.assertIsNone(SHARE.decide(self.payload(last_assistant_message='설명입니다. ' * 100), self.env))


if __name__ == '__main__':
    # The repository pre-push runner passes this flag to files mentioning it.
    if sys.argv[1:] == ['--selftest']:
        sys.argv.pop()
    unittest.main()
