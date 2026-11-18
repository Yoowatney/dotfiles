#!/usr/bin/env python3
"""Synthetic safe-reader regression tests; run with --selftest."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

READER = Path(__file__).with_name('session-user-messages.py')


class SafeUserMessagesTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(READER.is_file(), 'safe user-message reader is not implemented')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)

    def run_reader(self, records, relative='.codex/sessions/2026/01/01/fixture.jsonl'):
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('\n'.join(json.dumps(record) for record in records) + '\n')
        return subprocess.run([sys.executable, '-I', str(READER), str(path)],
                              env={**os.environ, 'HOME': str(self.home)},
                              text=True, capture_output=True)

    def test_only_user_text_is_returned_for_both_runtimes(self):
        for relative, user in (
            ('.codex/sessions/2026/01/01/fixture.jsonl', {'type': 'response_item', 'payload': {
                'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'Check the original request.'}]}}),
            ('.claude/projects/-fixture/fixture.jsonl', {'type': 'user', 'message': {
                'role': 'user', 'content': [{'type': 'text', 'text': 'Check the original request.'},
                                         {'type': 'tool_result', 'content': 'PRIVATE_TOOL_FIXTURE'}]}}),
        ):
            with self.subTest(relative=relative):
                result = self.run_reader([user,
                    {'type': 'response_item', 'payload': {'type': 'function_call_output', 'output': 'PRIVATE_TOOL_FIXTURE'}},
                    {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant', 'content': 'PRIVATE_ASSISTANT_FIXTURE'}},
                    {'type': 'assistant', 'message': {'content': 'PRIVATE_ASSISTANT_FIXTURE'}},
                    {'type': 'user', 'isMeta': True, 'message': {'content': 'PRIVATE_META_FIXTURE'}},
                    {'type': 'user', 'isCompactSummary': True, 'message': {'content': 'PRIVATE_SUMMARY_FIXTURE'}},
                    None, {'type': 'user', 'message': 'malformed'},
                ], relative)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)['messages'], ['Check the original request.'])
                self.assertNotIn('PRIVATE_', result.stdout)

    def test_apparent_credentials_and_code_are_redacted(self):
        text = ('Review the change. api_key="short-secret-fixture"; password=plain-fixture\n'
                'Authorization: Bearer bearer-fixture-value\n'
                'Use ghp_abcdefghijklmnopqrstuvwxyz123456 and opaque AbCdEf1234567890GhIjKlMnOpQrStUv.\n'
                '```sh\nprint-private-fixture\n```\n'
                '비밀번호: korean-secret-fixture\n'
                'https://user:url-password-fixture@example.test/?token=query-secret-fixture')
        result = self.run_reader([{'type': 'user', 'message': {'content': text}}])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Review the change.', result.stdout)
        self.assertIn('[REDACTED]', result.stdout)
        for secret in ('short-secret-fixture', 'plain-fixture', 'bearer-fixture-value',
                       'ghp_abcdefghijklmnopqrstuvwxyz123456', 'AbCdEf1234567890GhIjKlMnOpQrStUv',
                       'print-private-fixture', 'korean-secret-fixture', 'url-password-fixture', 'query-secret-fixture'):
            self.assertNotIn(secret, result.stdout)

    def test_codex_harness_and_claude_nonhuman_envelopes_are_excluded(self):
        result = self.run_reader([
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': 'PRIVATE_HARNESS_FIXTURE',
                'internal_chat_message_metadata_passthrough': {'content_item_metadata': [{'provenance': {'type': 'harness'}}]}}},
            {'type': 'user', 'origin': {'kind': 'tool'}, 'message': {'content': 'PRIVATE_NONHUMAN_FIXTURE'}},
            {'type': 'user', 'message': {'content': 'Keep ordinary requests.'}},
        ])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('PRIVATE_', result.stdout)
        self.assertIn('Keep ordinary requests.', result.stdout)

    def test_claude_queued_instruction_sent_mid_turn_is_returned_and_nonhuman_ones_are_not(self):
        # api#1355: a pane-tell typed while a turn runs is stored as a queued_command attachment, not a user record.
        queued = lambda prompt, kind: {'type': 'attachment', 'attachment': {
            'type': 'queued_command', 'prompt': prompt, 'commandMode': 'prompt', 'origin': {'kind': kind}}}
        result = self.run_reader([
            {'type': 'user', 'message': {'role': 'user', 'content': 'First request.'}},
            queued('Lead follow-up sent mid-turn.', 'human'),
            queued('PRIVATE_TASK_NOTIFICATION_FIXTURE', 'task-notification'),
            {'type': 'attachment', 'attachment': {'type': 'queued_command', 'prompt': 'PRIVATE_NO_ORIGIN_FIXTURE'}},
            {'type': 'attachment', 'attachment': {'type': 'queued_command', 'prompt': 'PRIVATE_SHELL_FIXTURE',
                                                  'commandMode': 'bash', 'origin': {'kind': 'human'}}},
            {'type': 'attachment', 'attachment': {'type': 'hook_success', 'content': 'PRIVATE_HOOK_FIXTURE'}},
            queued([{'type': 'text', 'text': 'Block-form follow-up.'}], 'human'),
        ], '.claude/projects/-fixture/fixture.jsonl')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['messages'],
                         ['First request.', 'Lead follow-up sent mid-turn.', 'Block-form follow-up.'])

    def test_nontranscript_and_symlink_escape_are_refused_without_content(self):
        outside = self.home / 'private.txt'
        outside.write_text('PRIVATE_OUTSIDE_FIXTURE')
        link = self.home / '.codex/sessions/2026/01/01/link.jsonl'
        link.parent.mkdir(parents=True)
        link.symlink_to(outside)
        for path in (outside, link):
            result = subprocess.run([sys.executable, '-I', str(READER), str(path)],
                                    env={**os.environ, 'HOME': str(self.home)}, text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('PRIVATE_OUTSIDE_FIXTURE', result.stdout + result.stderr)
            self.assertEqual(result.stdout, '')


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0]])
