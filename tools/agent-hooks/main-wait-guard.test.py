#!/usr/bin/env python3
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

HERE = Path(__file__).resolve().parent
HOOK = HERE / 'main-wait-guard.py'


class GuardTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.state = self.root / 'state/pane-tell'
        (self.root / 'state/orchestrator').mkdir(parents=True)
        (self.root / 'state/orchestrator/main').write_text('term_main\n')
        herdr = self.root / 'herdr'
        herdr.write_text('#!/bin/sh\nprintf \'{"result":{"pane":{"terminal_id":"%s"}}}\' "$FAKE_TERMINAL"\n')
        herdr.chmod(0o755)
        self.env = dict(os.environ, XDG_STATE_HOME=str(self.root / 'state'), HERDR_BIN_PATH=str(herdr),
                        HERDR_PANE_ID='wC:p1', FAKE_TERMINAL='term_main')

    def waiting(self, name, minutes, terminal='term_main', socket='default'):
        for directory, text in (('receivers', terminal + '\n' + socket + '\n'), ('waiting', 'secret body')):
            path = self.state / directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        self.age(path, minutes)
        return path

    def age(self, path, minutes):
        then = time.time() - minutes * 60
        os.utime(path, (then, then))

    def run_hook(self, tool='Poll', tool_input=None, **extra):
        payload = dict({'hook_event_name': 'PreToolUse', 'tool_name': tool, 'tool_input': tool_input or {},
                        'session_id': 's1'}, **extra)
        result = subprocess.run([sys.executable, '-I', str(HOOK)], input=json.dumps(payload), env=self.env,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        if not result.stdout.strip():
            return None
        output = json.loads(result.stdout)['hookSpecificOutput']
        self.assertEqual(output['permissionDecision'], 'deny')
        return output['permissionDecisionReason']

    def test_old_message_blocks_the_wait_once_with_one_line(self):
        path = self.waiting('1.old', 12)
        reason = self.run_hook()
        self.assertIn(str(path), reason)
        self.assertIn('12분', reason)
        self.assertNotIn('\n', reason)
        self.assertNotIn('secret body', reason)
        # The wait that has to happen (an execution approval) goes through on the next call.
        self.assertIsNone(self.run_hook())

    def test_another_message_growing_old_blocks_again(self):
        self.waiting('1.old', 12)
        self.assertIsNotNone(self.run_hook())
        newer = self.waiting('2.newer', 11)
        self.assertIn(str(newer), self.run_hook())
        self.assertIsNone(self.run_hook())

    def test_a_new_session_is_told_again(self):
        self.waiting('1.old', 12)
        self.assertIsNotNone(self.run_hook())
        self.assertIsNotNone(self.run_hook(session_id='s2'))

    def test_long_list_names_three_files_and_counts_the_rest(self):
        paths = [self.waiting('%d.m' % i, 30 - i) for i in range(5)]
        reason = self.run_hook()
        self.assertIn('5건', reason)
        self.assertIn(str(paths[0]), reason)
        self.assertNotIn(str(paths[3]), reason)
        self.assertIn('외 2건', reason)

    def test_held_batch_counts(self):
        path = self.state / 'batch/term_main/claimed/0000000001.000000.7'
        path.parent.mkdir(parents=True)
        path.write_text('x')
        (path.parent / '.meta').write_text('{}')
        self.age(path, 11)
        self.age(path.parent / '.meta', 90)
        self.assertIn(str(path), self.run_hook())

    def test_fresh_or_foreign_messages_do_not_block(self):
        self.waiting('1.fresh', 9)
        self.waiting('2.other', 60, terminal='term_other')
        self.waiting('3.workers', 60, socket='/x/sessions/workers/herdr.sock')
        orphan = self.waiting('4.delivered', 60)
        orphan.unlink()
        # Batch metadata that stays after delivery: its hex names can start with a digit.
        for name in ('jobs/1abc', 'finished/2def'):
            path = self.state / 'batch/term_main' / name
            path.parent.mkdir(parents=True)
            path.write_text('{}')
            self.age(path, 60)
        self.assertIsNone(self.run_hook())

    def test_only_the_main_pane_is_held(self):
        self.waiting('1.old', 12)
        self.env['FAKE_TERMINAL'] = 'term_worker'
        self.assertIsNone(self.run_hook())
        self.env['FAKE_TERMINAL'] = 'term_main'
        del self.env['HERDR_PANE_ID']
        self.assertIsNone(self.run_hook())

    def test_subagent_calls_pass(self):
        self.waiting('1.old', 12)
        self.assertIsNone(self.run_hook(agent_id='a1'))

    def test_waiting_commands_are_blocked(self):
        for tool, tool_input in (
                ('Bash', {'command': 'sleep 600; echo done'}),
                ('Bash', {'command': 'sleep 5m'}),
                ('Bash', {'command': 'gh run watch 123 --exit-status'}),
                ('Bash', {'command': 'gh pr checks 7 --watch'}),
                ('Bash', {'command': 'until test -f x; do sleep 5; done'}),
                ('REPL', {'code': 'await sleep(600000)'}),
                ('REPL', {'code': 'for (let i=0;i<40;i++){ if (ok) break; await sleep(5000) }'}),
        ):
            with self.subTest(tool_input=tool_input):
                self.waiting('1.old', 12)
                (self.state / 'wait-guard.json').unlink(missing_ok=True)
                self.assertIsNotNone(self.run_hook(tool, tool_input))

    def test_work_and_message_handling_commands_pass(self):
        self.waiting('1.old', 12)
        for tool, tool_input in (
                ('Bash', {'command': 'git status'}),
                ('Bash', {'command': 'sleep 5; herdr pane list'}),
                ('Bash', {'command': 'cat ' + str(self.state / 'waiting/1.old')}),
                ('Bash', {'command': 'pane-tell w1:p3 "sleep 600 later"'}),
                ('Bash', {'command': 'python3 main-channel.py post --thread 1.2 /tmp/a.txt; sleep 120'}),
                ('Bash', {'command': 'bash board.sh list; sleep 90'}),
                ('Bash', {'command': 'pane-spawn "job" "use gh pr checks --watch there"'}),
                ('Bash', {'command': 'sleep 600', 'run_in_background': True}),
                ('REPL', {'code': "await $`ls`.timeout(900000)"}),
                ('Read', {'file_path': '/tmp/x'}),
        ):
            with self.subTest(tool_input=tool_input):
                self.assertIsNone(self.run_hook(tool, tool_input))
        # None of those used up the one block.
        self.assertIsNotNone(self.run_hook())

    def test_broken_input_and_herdr_failure_never_block_or_crash(self):
        self.waiting('1.old', 12)
        for text in ('', 'not json', '[]', '{"tool_name":"Bash","tool_input":null,"hook_event_name":"PreToolUse"}'):
            result = subprocess.run([sys.executable, '-I', str(HOOK)], input=text, env=self.env,
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual((result.returncode, result.stdout), (0, ''), text)
        self.env['HERDR_BIN_PATH'] = str(self.root / 'missing')
        self.assertIsNone(self.run_hook())

    def test_unwritable_marker_does_not_block_every_wait(self):
        self.waiting('1.old', 12)
        (self.state / 'wait-guard.json').mkdir()
        self.assertIsNone(self.run_hook())


class InstallTest(unittest.TestCase):
    def test_install_is_idempotent_preserves_guards_and_removes_only_itself(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '.claude/settings.json'
            path.parent.mkdir()
            before = {'model': 'x', 'hooks': {'PreToolUse': [{'matcher': '*', 'hooks': [
                {'type': 'command', 'command': 'permission-guard'}]}]}}
            path.write_text(json.dumps(before))
            def run(*args):
                result = subprocess.run([sys.executable, str(HOOK), *args], env=dict(os.environ, HOME=tmp),
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                return json.loads(path.read_text())
            run('install')
            data = run('install')
            groups = data['hooks']['PreToolUse']
            self.assertEqual(groups[0], before['hooks']['PreToolUse'][0])
            self.assertEqual(len(groups), 2)
            self.assertEqual(groups[1]['matcher'], 'Poll|REPL|Bash')
            self.assertIn(str(HOOK), groups[1]['hooks'][0]['command'])
            self.assertEqual(data['model'], 'x')
            self.assertEqual(run('uninstall'), before)

    def test_uninstall_keeps_a_guard_sharing_the_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '.claude/settings.json'
            path.parent.mkdir()
            path.write_text('{}')
            env = dict(os.environ, HOME=tmp)
            subprocess.run([sys.executable, str(HOOK), 'install'], env=env, check=True)
            data = json.loads(path.read_text())
            other = {'type': 'command', 'command': 'permission-guard'}
            data['hooks']['PreToolUse'][0]['hooks'].append(other)
            path.write_text(json.dumps(data))
            subprocess.run([sys.executable, str(HOOK), 'uninstall'], env=env, check=True)
            self.assertEqual(json.loads(path.read_text())['hooks']['PreToolUse'][0]['hooks'], [other])

    def test_registered_command_allows_the_call_on_a_mac_without_the_file(self):
        # python3 exits 2 on a missing script, which Claude Code reads as "block this tool call".
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, HOME=tmp)
            path = Path(tmp) / '.claude/settings.json'
            path.parent.mkdir()
            subprocess.run([sys.executable, str(HOOK), 'install'], env=env, check=True)
            command = json.loads(path.read_text())['hooks']['PreToolUse'][0]['hooks'][0]['command']
            present = subprocess.run(['sh', '-c', command], input='{}', capture_output=True, text=True)
            self.assertEqual(present.returncode, 0, present.stderr)
            missing = subprocess.run(['sh', '-c', command.replace(str(HOOK), tmp + '/gone.py')], input='{}',
                                     capture_output=True, text=True)
            self.assertEqual(missing.returncode, 0, missing.stderr)


if __name__ == '__main__':
    unittest.main()
