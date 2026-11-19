#!/usr/bin/env python3
"""Exercise the installed hook entry point. No signal command is ever executed."""
import json
import importlib.util
from unittest import mock
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import sys

HOOK = Path(__file__).with_name('no-sed-inplace.py')


class ProcessTargetGuardTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.log = self.root / 'calls'
        for name in ('pgrep', 'ps'):
            path = self.bin / name
            path.write_text('#!/bin/sh\n'
                            f'printf "%s\\n" "{name} $*" >> "{self.log}"\n'
                            + ('printf "%s" "$MATCHES"; exit "${QUERY_STATUS:-0}"\n' if name == 'pgrep'
                               else 'printf "%s" "$PROCESSES"; exit "${PS_STATUS:-0}"\n'))
            path.chmod(0o755)

    def check(self, command, protected=True, **env):
        result = subprocess.run(['python3', '-I', str(HOOK)], text=True, capture_output=True,
                                input=json.dumps({'tool_name': 'Bash', 'tool_input': {'command': command}}),
                                env=dict(os.environ, **dict(PATH=f'{self.bin}:{os.environ["PATH"]}',
                                         MATCHES='123\n456\n',
                                         PROCESSES='123 /opt/bin/claude\n456 /usr/bin/worker\n') | env), timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        reason = json.loads(result.stdout)['hookSpecificOutput']['permissionDecisionReason'] if result.stdout.strip() else ''
        if protected:
            self.assertIn('process-target-guard:', reason, (command, reason))
        else:
            self.assertEqual(reason, '', (command, reason))
        return self.log.read_text() if self.log.exists() else ''

    def test_pkill_selectors_are_preflighted_without_running_pkill(self):
        for command, query in [
            ('pkill -f obsidian', 'pgrep -f -- obsidian'),
            ('/usr/bin/pkill -TERM -if obsidian', 'pgrep -i -f -- obsidian'),
            ('env X=1 nohup pkill -9 -u 501 -x claude', 'pgrep -u 501 -x -- claude'),
            ('sudo -u root pkill -f obsidian', 'pgrep -f -- obsidian'),
            ("bash -lc 'pkill -f obsidian'", 'pgrep -f -- obsidian'),
        ]:
            with self.subTest(command=command):
                self.assertIn(query, self.check(command))

    def test_killall_uses_exact_names_and_regex_when_requested(self):
        ancestors = '-a ' if sys.platform == 'darwin' else ''
        self.assertIn(f'pgrep {ancestors}-x -- claude', self.check('killall claude'))
        self.assertIn(f'pgrep {ancestors}-i -x -- Claude', self.check('killall -i -TERM Claude'))
        self.assertIn(f'pgrep {ancestors}-- cl.*', self.check("killall -m 'cl.*'"))
        self.check('killall worker codex')

    def test_pgrep_and_kill_combinations(self):
        for command in [
            'kill $(pgrep -f obsidian)', 'kill -TERM `pgrep -f obsidian`',
            'pgrep -f obsidian | xargs kill', 'pgrep -f obsidian | xargs -r /bin/kill -9',
            'pids=$(pgrep -f obsidian); kill "$pids"',
            'for pid in $(pgrep -f obsidian); do kill "$pid"; done',
            'pgrep -f obsidian | while read pid; do kill "$pid"; done',
            "sh -c 'kill $(pgrep -f obsidian)'",
        ]:
            with self.subTest(command=command):
                self.assertIn('pgrep -f -- obsidian', self.check(command))

    def test_every_protected_executable_and_false_match(self):
        for executable in ('claude', 'codex', 'herdr'):
            self.check('pkill -f obsidian', PROCESSES=f'123 /opt/bin/{executable}\n')
        self.check('pkill -f obsidian', protected=False,
                   PROCESSES='123 /usr/bin/python3\n456 /usr/bin/obsidian\n')

    def test_safe_reads_text_and_nonmatching_targets(self):
        for command in ['pgrep -f obsidian', "printf '%s' 'pkill -f obsidian'",
                        "git log --grep='kill $(pgrep -f obsidian)'",
                        "cat <<'EOF'\npkill -f obsidian\nEOF"]:
            with self.subTest(command=command):
                self.assertEqual(self.check(command, protected=False), '')
        self.check('pkill -f unique-worker', protected=False, MATCHES='', QUERY_STATUS='1')

    def test_query_failures_and_unresolved_arguments_fail_closed(self):
        self.check('pkill -f obsidian', QUERY_STATUS='2')
        self.check('killall worker', PS_STATUS='1')
        self.check('pkill -f "$pattern"')
        self.check('pkill --unsupported obsidian')

    def test_review_regressions(self):
        for command in ['true&&(pkill -f obsidian)',
                        'case stop in\n stop)\n pkill -f obsidian\n ;;\nesac',
                        'noglob pkill -f obsidian', 'nocorrect pkill -f obsidian',
                        'case stop in stop) pkill -f obsidian;; esac',
                        r'bash -c "kill \$(pgrep -f obsidian)"',
                        r'bash -c "kill \`pgrep -f obsidian\`"',
                        "bash <<< 'pkill -f obsidian'",
                        'time -p pkill -f obsidian', 'exec -a task pkill -f obsidian',
                        '2>/dev/null pkill -f obsidian',
                        '{ pkill -f obsidian; }', 'sudo --user root pkill -f obsidian',
                        'sudo --group staff pkill -f obsidian',
                        'builtin kill $(pgrep -f obsidian)', 'command -p pkill -f obsidian',
                        'pgrep -f obsidian | xargs -E STOP kill',
                        "env bash <<'EOF'\npkill -f obsidian\nEOF",
                        "sudo bash <<'EOF'\npkill -f obsidian\nEOF"]:
            with self.subTest(command=command):
                self.assertIn('pgrep -f -- obsidian', self.check(command))
        if sys.platform == 'darwin':
            self.assertIn('pgrep -a -x -- claude', self.check('killall claude'))
        for command in ['kill -- -$(pgrep -x worker)',
                        'pgrep -x worker | xargs -I{} kill -- -{}',
                        'pids=-$(pgrep -x worker); kill "$pids"',
                        'pgrep -x worker; kill 0', 'pgrep -x worker; kill 00',
                        'pgrep -x worker; kill +0',
                        "kill $(pgrep -x worker | awk '{print -$1}')",
                        "pgrep -x worker | awk '{print -$1}' | xargs kill",
                        "pgrep -d ' -' -x worker | xargs kill --",
                        'pgrep -c worker | xargs kill',
                        'pgrep -x worker; kill -- *', 'kill $(pgrep -x worker) 987',
                        'killall c?dex', 'pkill -f cl*']:
            self.check(command, QUERY_STATUS='1', MATCHES='')
        self.check('kill $(pgrep -x worker)', protected=False,
                   PROCESSES='123 worker\n456 worker\n')
        self.check('pgrep -x worker | xargs kill', protected=False,
                   PROCESSES='123 worker\n456 worker\n')
        for processes in ['', '456 worker\n']:
            self.check('pkill -f obsidian', PROCESSES=processes)

    def test_codex_review_regressions(self):
        # xargs replacement strings hide the selector from the preflight: refuse them.
        self.check('printf obsidian | xargs -I PATTERN pkill -f PATTERN', MATCHES='', QUERY_STATUS='1')
        self.check('printf obsidian | xargs -JPATTERN pkill -f PATTERN', MATCHES='', QUERY_STATUS='1')
        # Wrapper option strictness applies only around a kill tool.
        self.check('/usr/bin/time -l /bin/echo skills', protected=False)
        self.check('time -l bash -c "pkill -f obsidian"', MATCHES='', QUERY_STATUS='1')
        # A $ inside single quotes is a regex anchor, not a variable.
        self.assertIn('pgrep -f -- ^unique-worker$',
                      self.check("pkill -f '^unique-worker$'", protected=False, MATCHES='', QUERY_STATUS='1'))
        self.check("bash -c 'pkill -f \"$pattern\"'", MATCHES='', QUERY_STATUS='1')

    def test_ordinary_commands_do_not_load_process_inspection(self):
        for command in ['printf safe', 'git status --short', 'pgrep -f obsidian']:
            self.assertEqual(self.check(command, protected=False), '')

    def test_fast_path_avoids_module_load_and_subprocesses(self):
        spec = importlib.util.spec_from_file_location('guard_fast_path', HOOK)
        guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(guard)
        with mock.patch.object(guard.importlib.util, 'spec_from_file_location',
                               side_effect=AssertionError('unexpected module load')), \
             mock.patch.object(guard.subprocess, 'run',
                               side_effect=AssertionError('unexpected process query')):
            for command in ['printf safe', 'git status --short', 'pgrep -f obsidian']:
                self.assertIsNone(guard.process_target_problem(command))
        self.assertIsNone(guard._process_guard)


if __name__ == '__main__':
    unittest.main()
