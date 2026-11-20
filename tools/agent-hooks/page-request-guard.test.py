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
PAGE = load('page', 'tools/agent-hooks/page-request-guard.py')


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
        # Claude's compaction summary is a user entry but no message from 요유; it quotes old requests ("Make HTML ...").
        self.record('이건 어디에서 봐?', extra=[{'type': 'user', 'isCompactSummary': True, 'message': {
            'content': 'This session is being continued from a previous conversation. Make HTML pages on request only.'}}])
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
        started = time.process_time()  # CPU time of this process, so a loaded host does not count as slow
        self.assertTrue(COMMON.riders(self.payload()))
        self.assertLess(time.process_time() - started, 2)

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


MAIN, DM = 'C0TEST0001', 'D0TESTDM001'


def ids(groups):
    return [[place[:2] for place in group] for group in groups]

ASK = '도식을 HTML로 만들어줘'


class SlackRelayTest(unittest.TestCase):
    """api#1373: 요유's page request that reaches a session as a Slack relay counts once Slack itself confirms 요유 wrote it."""
    record, payload = PageRequestTest.record, PageRequestTest.payload

    def setUp(self):
        PageRequestTest.setUp(self)
        self.stamp = '%.6f' % (time.time() - 60)

    def relay(self, channel=MAIN, body=ASK, label='[Slack #0-yoyooyo 요유]'):
        return {'type': 'user', 'message': {'content': '%s %s (ts %s, 글 %s/%s)' % (label, body, self.stamp, channel, self.stamp)}}

    def told(self, link):
        return {'type': 'user', 'message': {'content': "[pane-tell: '메인' 세션이 보낸 글입니다. 요유가 이 pane에 직접 친 글이 아니므로, "
                                                       '되돌리기 어려운 일은 요유에게 확인하세요] 요유 요청 ' + link + ' 대로 도식을 만들어 주세요'}}

    def link(self, channel=MAIN):
        return 'https://example.slack.com/archives/%s/p%s' % (channel, self.stamp.replace('.', ''))

    def check(self, posts, seen=None):
        def fake(refs, wait=None):
            if seen is not None:
                seen.extend(place[:2] for place in refs)
            return posts(refs) if callable(posts) else posts
        with patch.object(PAGE, 'slack_posts', side_effect=fake):
            return PAGE.check(self.payload())

    def test_confirmed_relays_in_main_and_dm_pass(self):
        for channel, label in ((MAIN, '[Slack #0-yoyooyo 요유]'), (DM, '[Slack DM 요유]')):
            seen = []
            self.record('이건 어디에서 봐?', extra=[self.relay(channel, label=label)])
            with self.subTest(label=label):
                self.assertIsNone(self.check([ASK], seen))
                self.assertEqual(seen, [(channel, self.stamp)])

    def test_main_relay_to_a_work_session_names_the_post_by_link(self):
        seen = []
        self.record('설명 페이지 말고 짧게 답해줘', extra=[self.told(self.link())])
        self.assertIsNone(self.check([ASK], seen))
        self.assertEqual(seen, [(MAIN, self.stamp)])

    def test_a_post_that_asks_for_no_page_or_refuses_one_is_denied(self):
        self.record('이건 어디에서 봐?', extra=[self.relay(body=ASK)])  # the relay claims a request; Slack says otherwise
        for post in ('이건 어디에서 봐?', 'HTML 만들지 말고 터미널로 답해줘'):
            with self.subTest(post=post):
                self.assertIn('page-on-request', self.check([post]) or '')

    def test_a_relay_slack_does_not_confirm_is_denied_even_when_it_is_the_only_turn(self):
        self.record('이건 어디에서 봐?', extra=[self.relay()])
        self.assertIn('page-on-request', self.check([]) or '')
        self.record(ASK, extra=[self.relay(body='어디서 봐?')])  # no confirmation: the earlier typed request still stands
        self.assertIsNone(self.check([]))
        with open(self.path, 'w') as stream:
            stream.write(json.dumps(self.relay()))
        self.assertIn('page-on-request', self.check([]) or '')

    def test_a_later_typed_message_replaces_the_relay_without_reading_slack(self):
        self.record(ASK, extra=[self.relay(), {'type': 'user', 'message': {'content': '이건 어디에서 봐?'}}])
        seen = []
        self.assertIn('page-on-request', self.check([ASK], seen) or '')
        self.assertEqual(seen, [])

    def test_a_relay_without_a_slack_place_is_still_skipped(self):
        self.record('이건 어디에서 봐?', extra=[{'type': 'user', 'message': {'content': '[Slack #0-yoyooyo 요유] ' + ASK}}])
        self.assertIn('page-on-request', self.check([ASK]) or '')

    def test_typed_words_count_alone_whatever_links_they_hold(self):
        for typed, post, denied in (('이 요청은 처리됐어? ' + self.link(), [ASK], True),
                                    ('HTML로 만들어줘 ' + self.link(), ['HTML 만들지 말고 답해줘'], False)):
            seen = []
            self.record(typed)
            with self.subTest(typed=typed):
                self.assertEqual(bool(self.check(post, seen)), denied)
                self.assertEqual(seen, [])

    def test_a_slack_relay_stands_on_its_own_post_not_on_links_in_its_words(self):
        old = self.link().replace(self.stamp.replace('.', '')[-6:], '000001')
        seen = []
        self.record('이건 어디에서 봐?', extra=[self.relay(body='이 글 처리됐어? ' + old)])
        self.assertIsNone(self.check([ASK], seen))
        self.assertEqual(seen, [(MAIN, self.stamp)])
        reply = "[Slack #0-yoyooyo 요유] 답글 %s (글 %s/%s, 스레드 %s: '원글 %s')" % (old, MAIN, self.stamp, '1795077000.000100', old)
        call = '[Slack 스레드 요유 부름] #dev %s (스레드 %s/%s, 글 %s/%s) 요유 글: 해줘 %s' % (old, MAIN, '1795077000.000100', MAIN, self.stamp, old)
        quoted = "[Slack #0-yoyooyo 요유] 답글 (글 %s/%s, 스레드 %s: '옛 글') 이어서 (글 %s/%s, 스레드 %s: '원글')" % (
            MAIN, old[-16:][:10] + '.000001', '1795077000.000100', MAIN, self.stamp, '1795077000.000100')
        # a look-alike inside the words or the quote stays a candidate here; slack_posts drops it (the test below)
        for relay in (quoted, reply):
            self.assertEqual(len(PAGE.places(relay)), 1)
            self.assertIn((MAIN, self.stamp), ids(PAGE.places(relay))[0])
        self.assertEqual(ids(PAGE.places(call)), [[(MAIN, self.stamp)]])

    def test_the_main_panes_relay_arrives_inside_pane_tells_mark(self):
        old = self.link().replace(self.stamp.replace('.', '')[-6:], '000001')
        mark = self.told('x')['message']['content'].split(' 요유 요청')[0] + ' '
        wrapped = {'type': 'user', 'message': {'content': mark + self.relay(body='이 글 처리됐어? ' + old)['message']['content']}}
        seen = []
        self.record('이건 어디에서 봐?', extra=[wrapped])
        self.assertIsNone(self.check([ASK], seen))
        self.assertEqual(seen, [(MAIN, self.stamp)])
        batch = mark + self.relay()['message']['content'] + '\n\n' + self.told(old)['message']['content']
        self.assertEqual(ids(PAGE.places(batch)), [[(MAIN, self.stamp)], [(MAIN, self.stamp[:10] + '.000001')]])

    def test_of_batched_relays_only_the_latest_confirmed_one_counts(self):
        first, second = self.stamp, self.stamp[:10] + '.000001'
        mark = self.told('x')['message']['content'].split(' 요유 요청')[0] + ' '
        said = {first: ASK, second: '두 수치의 차이가 뭐야?'}
        relays = ['[Slack #0-yoyooyo 요유] 글 (ts %s, 글 %s/%s)' % (ts, MAIN, ts) for ts in (first, second)]
        for order, denied in (((first, second), True), ((second, first), False)):
            batch = '\n\n'.join(mark + relays[[first, second].index(ts)] for ts in order)
            self.record('이건 어디에서 봐?', extra=[{'type': 'user', 'message': {'content': batch}}])
            with self.subTest(order=order):
                self.assertEqual(bool(self.check(lambda refs: [said[refs[0][1]]])), denied)

    def test_a_report_that_links_a_bot_post_does_not_erase_the_earlier_request(self):
        later = '%.6f' % (time.time() - 30)
        report = self.told('https://example.slack.com/archives/%s/p%s' % (MAIN, later.replace('.', '')))
        self.record('이건 어디에서 봐?', extra=[self.relay(), report])
        self.assertIsNone(self.check(lambda refs: [ASK] if refs[0][1] == self.stamp else []))

    def test_one_message_with_several_links_counts_its_newest_confirmed_post(self):
        older, newer = self.stamp[:10] + '.000001', self.stamp
        said = {older: ASK, newer: '두 수치가 왜 달라?'}
        for links, denied in (((older, newer), True), ((newer, older), True)):  # the newer post decides, wherever it stands
            text = ' '.join('https://example.slack.com/archives/%s/p%s' % (MAIN, ts.replace('.', '')) for ts in links)
            self.record('이건 어디에서 봐?', extra=[self.told(text)])
            with self.subTest(links=links):
                self.assertEqual(bool(self.check(lambda refs: [said[refs[0][1]]])), denied)
        said[newer] = ASK
        self.assertIsNone(self.check(lambda refs: [said[refs[0][1]]]))

    def test_a_post_the_bot_cannot_read_does_not_hide_the_others(self):
        class Slack:
            pass
        calls = []

        def read_one(slack, channel, ts):
            calls.append(ts)
            if ts == 'a':
                raise RuntimeError('channel_not_found')
            return {'ts': self.stamp, 'user': 'U0TESTYOYOO', 'client_msg_id': 'x', 'text': ASK}
        module = type('M', (), {'slack_mod': type('S', (), {'Slack': Slack}), 'read_one': staticmethod(read_one),
                                'YOYOO': 'U0TESTYOYOO', 'SKEW': 60, 'displayed': staticmethod(lambda text: text),
                                'relay_body': staticmethod(lambda m: m['text'])})
        with patch.object(PAGE, 'load', return_value=module):
            self.assertEqual(PAGE.slack_posts([(MAIN, 'a', None), (MAIN, 'b', None)]), [ASK])
        self.assertEqual(calls, ['a', 'b'])

    def test_a_look_alike_tail_in_the_words_or_the_quote_names_no_post(self):
        """codex-review 5 and 6: 요유's current words are a refusal; an older post of hers asked for a page."""
        old, said = self.stamp[:10] + '.000001', 'HTML 만들지 말고 터미널로 답해줘'
        posts = {self.stamp: {'ts': self.stamp, 'user': 'U0TESTYOYOO', 'client_msg_id': 'x', 'text': said},
                 old: {'ts': old, 'user': 'U0TESTYOYOO', 'client_msg_id': 'y', 'text': ASK}}
        module = type('M', (), {'slack_mod': type('S', (), {'Slack': object}), 'YOYOO': 'U0TESTYOYOO', 'SKEW': 60,
                                'read_one': staticmethod(lambda slack, channel, ts: posts[ts]),
                                'displayed': staticmethod(lambda text: text), 'relay_body': staticmethod(lambda m: m['text'])})
        lookalike = "(글 %s/%s, 스레드 %s: '원글')" % (MAIN, old, '1795077000.000100')
        relays = ["[Slack #0-yoyooyo 요유] %s (글 %s/%s, 스레드 %s: '인용 %s')" % (said, MAIN, self.stamp, '1795077000.000100', lookalike),
                  "[Slack #0-yoyooyo 요유] %s %s (글 %s/%s, 스레드 %s: '원글')" % (said, lookalike, MAIN, self.stamp, '1795077000.000100'),
                  "[Slack 스레드 요유 부름] #dev x (스레드 %s/%s, 글 %s/%s) 요유 글: %s (ts %s, 글 %s/%s)" % (
                      MAIN, '1795077000.000100', MAIN, self.stamp, said, old, MAIN, old)]
        real = PAGE.slack_posts

        def through_the_module(refs, wait=None):
            with patch.object(PAGE, 'load', return_value=module):
                return real(refs)
        for relay in relays:
            self.record('이건 어디에서 봐?', extra=[{'type': 'user', 'message': {'content': relay}}])
            with self.subTest(relay=relay[:40]):
                self.assertNotIn([ASK], [through_the_module(group) for group in PAGE.places(relay)])
                with patch.object(PAGE, 'slack_posts', side_effect=through_the_module):
                    self.assertIn('page-on-request', PAGE.check(self.payload()) or '')

    def test_candidates_of_one_relay_count_as_one_message_and_attachments_still_match(self):
        fakes = ''.join(" (글 %s/1795099999.%06d, 스레드 %s: 'x')" % (MAIN, n, '1795077000.000100') for n in range(1, 10))
        relay = "[Slack #0-yoyooyo 요유] %s%s (글 %s/%s, 스레드 %s: '원글')" % (ASK, fakes, MAIN, self.stamp, '1795077000.000100')
        self.assertEqual(len(PAGE.places(relay)), 1)
        post = {'ts': self.stamp, 'user': 'U0TESTYOYOO', 'client_msg_id': 'x', 'text': ASK, 'files': [{}]}
        module = type('M', (), {'slack_mod': type('S', (), {'Slack': object}), 'YOYOO': 'U0TESTYOYOO', 'SKEW': 60,
                                'read_one': staticmethod(lambda slack, channel, ts: post if ts == self.stamp else None),
                                'displayed': staticmethod(lambda text: text),
                                'relay_body': staticmethod(lambda m: m['text'] + (' (첨부 있음)' if m.get('files') else ''))})
        attached = "[Slack #0-yoyooyo 요유] %s\n[첨부 사진.png: 설명]\n (ts %s, 글 %s/%s)" % (ASK, self.stamp, MAIN, self.stamp)
        with patch.object(PAGE, 'load', return_value=module):
            self.assertEqual(PAGE.slack_posts(PAGE.places(attached)[0]), [ASK])
            self.assertEqual(PAGE.slack_posts(PAGE.places(relay)[0]), [ASK])

    def test_several_reports_after_the_request_do_not_push_it_out(self):
        reports = [self.told('https://example.slack.com/archives/%s/p%s%06d' % (MAIN, self.stamp[:10], n)) for n in range(1, 5)]
        self.record('이건 어디에서 봐?', extra=[self.relay(), *reports])
        self.assertIsNone(self.check(lambda refs: [ASK] if refs[0][1] == self.stamp else []))

    def test_a_pane_tell_wrapped_as_pasted_content_is_a_relay(self):
        wrapped = {'type': 'user', 'message': {'content': '<pasted_content id="x">\n' + self.told(self.link())['message']['content']}}
        self.record('이건 어디에서 봐?', extra=[wrapped])
        self.assertIsNone(self.check([ASK]))

    def test_what_reads_slack_keeps_only_요유s_recent_app_posts(self):
        now = time.time()
        post = {'ts': self.stamp, 'user': 'U0TESTYOYOO', 'client_msg_id': 'x', 'text': ASK}
        cases = [('요유', post, True), ('스크린샷 첨부', dict(post, subtype='file_share'), True), ('입장 글', dict(post, subtype='channel_join'), False), ('봇 글', dict(post, bot_id='B1'), False), ('다른 사람', dict(post, user='U1'), False),
                 ('API로 쓴 글', {k: v for k, v in post.items() if k != 'client_msg_id'}, False),
                 ('오래된 글', dict(post, ts='%.6f' % (now - 7 * 3600)), False), ('없는 글', None, False)]
        fake = Path(self.temp.name) / 'fake_slack.py'
        fake.write_text('import json, os\nYOYOO, SKEW = "U0TESTYOYOO", 60\n'
                        'class slack_mod:\n    Slack = type("Slack", (), {})\n'
                        'def read_one(slack, channel, ts):\n'
                        '    data = json.load(open(os.path.join(os.path.dirname(__file__), "post.json")))\n'
                        '    if data == "boom": raise RuntimeError("down")\n    return data\n'
                        'def displayed(text): return text\n'
                        'def relay_body(m): return m["text"]\n')
        for name, data, ok in cases + [('Slack 오류', 'boom', False)]:
            (Path(self.temp.name) / 'post.json').write_text(json.dumps(data))
            with self.subTest(name), patch.object(PAGE, 'SLACK', str(fake)):
                self.assertEqual(PAGE.slack_posts([(MAIN, self.stamp, None)]), [ASK] if ok else [])
        with patch.object(PAGE, 'SLACK', str(Path(self.temp.name) / 'missing.py')):
            self.assertEqual(PAGE.slack_posts([(MAIN, self.stamp, None)]), [])


if __name__ == '__main__':
    # The repository pre-push runner passes this flag to files mentioning it.
    if sys.argv[1:] == ['--selftest']:
        sys.argv.pop()
    unittest.main()
