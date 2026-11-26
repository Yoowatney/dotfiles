#!/usr/bin/env python3
"""All commands are inert hook inputs: never start an Actions run to test a guard."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock

ROOT = Path(__file__).resolve().parents[2]
ENTRY = ROOT / 'tools/claude/hooks/gcp-account-guard.py'
SPEC = importlib.util.spec_from_file_location('common_guard', ENTRY)
COMMON = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COMMON)


# No test reaches GitHub: every lookup the guard makes goes to this stand-in for gh.
# Rerunning a pull_request run of an older commit cancels the newer commit's checks under cancel-in-progress.
FAKE_GH = '''#!/usr/bin/env python3
import json, os, sys, time
args = sys.argv[1:]
if 'FAKE_GH_LOG' in os.environ:
    with open(os.environ['FAKE_GH_LOG'], 'a') as log:
        log.write(json.dumps([os.environ.get('GH_REPO'), args]) + chr(10))
runs = {'1': ('push', 'main', 'old'), '123': ('push', 'main', 'old'), '333': ('push', 'main', 'old'),
        '111': ('pull_request', 'bump', 'old'), '222': ('pull_request', 'bump', 'new'),
        '444': ('pull_request', 'gone', 'old')}
if args[:2] == ['run', 'view']:
    if args[2] == '555':
        time.sleep(30)
    if args[2] not in runs:
        sys.exit(1)
    print(json.dumps(dict(zip(('event', 'headBranch', 'headSha'), runs[args[2]]))))
elif args[:2] == ['pr', 'list']:
    branch = args[args.index('--head') + 1]
    print(json.dumps([{'number': 7, 'headRefOid': 'new'}] if branch == 'bump' else []))
elif args[0] == 'api' and args[1].endswith('/actions/jobs/9'):
    print(json.dumps({'run_id': 111}))
else:
    sys.exit(1)
'''


def setUpModule():
    directory = tempfile.TemporaryDirectory()
    unittest.addModuleCleanup(directory.cleanup)
    gh = Path(directory.name) / 'gh'
    gh.write_text(FAKE_GH)
    gh.chmod(0o755)
    path = unittest.mock.patch.dict(os.environ, {'PATH': directory.name + os.pathsep + os.environ['PATH']})
    path.start()
    unittest.addModuleCleanup(path.stop)


class ActionsGuardTest(unittest.TestCase):
    def verdict(self, command, tool='Bash'):
        return COMMON.riders({'tool_name': tool, 'tool_input': {'command': command}})

    def test_manual_execution_is_denied_at_existing_common_entry(self):
        commands = [
            'gh workflow run build.yml', 'gh run rerun 123 --failed',
            'gh workflow enable build.yml', '/opt/homebrew/bin/gh workflow run build.yml',
            'gh -R owner/repo workflow run build.yml', 'gh --repo=owner/repo run rerun 123',
            'gh workflow --repo owner/repo run build.yml',
            'timeout 30 gh workflow run build.yml',
            'env GH_HOST=github.com gh workflow run build.yml',
            'cd /tmp && gh workflow run build.yml', 'true; gh run rerun 123',
            'true\ngh workflow enable build.yml', 'gh workflow ' + '\\\n' + ' run build.yml',
            'bash -lc "gh workflow run build.yml"',
            'bash --norc -lc "gh workflow run build.yml"',
            'result="$(gh workflow run build.yml)"',
            'echo "$(gh run rerun 123)"',
            'echo `gh workflow enable build.yml`',
            "bash <<'SCRIPT'\ngh workflow run build.yml\nSCRIPT",
            'cat <<EOF\n$(gh workflow run build.yml)\nEOF',
            "gh api -X POST repos/owner/repo/actions/workflows/build.yml/dispatches -f ref=main",
            "gh api repos/{owner}/{repo}/actions/workflows/1/dispatches --method=POST --input body.json",
            "gh api /repos/owner/repo/actions/workflows/build.yml/dispatches -f ref=main",
            "gh api --method POST https://api.github.com/repos/owner/repo/actions/runs/123/rerun-failed-jobs",
            "gh api -XPOST repos/owner/repo/actions/runs/123/jobs/456/rerun",
            "gh api -X POST repos/owner/repo/actions/jobs/456/rerun",
            "gh api -X PUT repos/owner/repo/actions/workflows/build.yml/enable",
            "gh api -X POST repos/owner/repo/dispatches -f event_type=build",
        ]
        for command in commands:
            with self.subTest(command=command):
                reason = self.verdict(command)
                self.assertTrue(reason, command)
                self.assertIn('Actions', reason)
                self.assertIn('ACTIONS_RUN_REASON', reason)

    def test_release_push_and_read_only_commands_pass(self):
        for command in [
            './deploy-release.sh staging', 'bash scripts/deploy-release.sh --staging',
            'gh release create v1.2.3 --prerelease', 'git push origin feature',
            'gh pr create --title test --body test', 'gh run list', 'gh run view 123 --log',
            'gh workflow list', 'gh workflow view build.yml',
            'gh api repos/owner/repo/actions/workflows/build.yml/dispatches -X GET',
            'gh api repos/owner/repo/actions/runs/123',
            'gh api -X POST repos/owner/repo/releases -f tag_name=v1.2.3',
            'gh api repos/owner/repo/actions/workflows/build.yml/dispatches',
            "echo 'gh workflow run build.yml'", "rg 'gh run rerun' tools",
            'gh workflow disable build.yml',
            'gh workflow run --help', 'gh help workflow run',
            "echo '$(gh workflow run build.yml)'", 'echo "\\$(gh workflow run build.yml)"',
            "# gh workflow run build.yml\ntrue",
            "gh api -X POST repos/owner/repo/releases -f body='gh workflow run build.yml'",
            "cat <<'EOF' > example.txt\ngh workflow run build.yml\nEOF",
            "cat <<'EOF'\n$(gh workflow run build.yml)\nEOF",
            'cat <<EOF\ngh workflow run build.yml\nEOF',
        ]:
            with self.subTest(command=command):
                self.assertIsNone(self.verdict(command))
        self.assertIsNone(self.verdict('gh workflow run x', tool='Read'))

    def test_a_comment_or_hash_does_not_hide_the_next_command(self):
        # shlex drops from # to the end of the line, newline included, so the next command became an argument.
        for command in [
            'true # c\ngh workflow run build', 'echo ${#a} ; gh run rerun 1', 'echo a#b; gh workflow run build',
            "true # it's done\ngh workflow run build", 'x="$(true # c\ngh workflow run build)"',
            "bash -c 'true # c\ngh workflow run build'",
            'true # c \\\ngh workflow run build',  # a backslash does not continue a comment
        ]:
            with self.subTest(command=command):
                self.assertIn('ACTIONS_RUN_REASON', self.verdict(command) or '', command)
        self.assertIn('ci-watch.py', self.verdict('true # c\ngh run watch 1') or '')
        for command in [
            'true # gh workflow run build', 'true # c; gh run rerun 1', 'true;# gh run watch 1',
            'ls # then $(gh workflow run build)\ntrue', 'echo "# not a comment" # gh workflow run build',
        ]:
            with self.subTest(command=command):
                self.assertIsNone(self.verdict(command), command)

    def test_a_comment_inside_backticks_ends_at_the_closing_backtick(self):
        # Removing the closing backtick with the comment left the command inside unjudged (regression of #173).
        for command in ['x=`gh workflow run build # c`', 'echo `gh run rerun 1 # retry`',
                        'echo "`gh run rerun 1 # c`"',
                        'x=$(echo `gh run rerun 1 # c`)']:
            with self.subTest(command=command):
                self.assertIn('ACTIONS_RUN_REASON', self.verdict(command) or '', command)
        self.assertIn('ci-watch.py', self.verdict('x=`gh run watch 1 # c`') or '')
        self.assertIsNone(self.verdict("gh pr view 1 `# it's a note about rerun`"))

    def test_an_unquoted_dollar_or_hash_in_an_assignment_hides_nothing(self):
        # The value split at $ or #, so the rest was read as the command name.
        for command in ['GH_TOKEN=$TOKEN gh workflow run build', 'ACTIONS_RUN_REASON=$REASON gh workflow run build',
                        'FOO=a#b gh workflow run build']:
            with self.subTest(command=command):
                self.assertIn('ACTIONS_RUN_REASON', self.verdict(command) or '', command)

    def test_watch_requires_the_shared_locked_entry_even_with_a_reason(self):
        for command in ['gh run watch 123', 'gh -R owner/repo run watch 123 --interval 60',
                        "ACTIONS_RUN_REASON='required recovery' gh run watch 123",
                        'bash -lc "gh run watch 123"', 'x=$(gh run watch 123)',
                        'gh run --repo owner/repo watch 123',
                        'timeout 30 gh run watch 123', 'gtimeout -k 5 30s gh run watch 123',
                        'nice -n 5 gh run watch 123', 'gnice --adjustment=5 gh run watch 123',
                        'stdbuf -o L gh run watch 123', 'gstdbuf --output=L gh run watch 123']:
            with self.subTest(command=command):
                self.assertIn('ci-watch.py', self.verdict(command) or '')
        for command in ['gh run watch --help', "echo 'gh run watch 123'",
                        "timeout 30 printf '%s' 'gh run watch 123'",
                        'uv run --no-project python3 ~/.dotfiles/tools/agent-hooks/ci-watch.py --repo owner/repo 123']:
            self.assertIsNone(self.verdict(command))

    def test_reason_is_required_on_each_command_and_does_not_leak(self):
        for command in [
            "ACTIONS_RUN_REASON='required release recovery' gh workflow run build.yml",
            "env ACTIONS_RUN_REASON='required recovery' gh run rerun 123",
            "ACTIONS_RUN_REASON='required recovery' gh api -X POST repos/o/r/actions/workflows/1/dispatches",
            "bash -lc \"ACTIONS_RUN_REASON='required recovery' gh workflow run build.yml\"",
        ]:
            with self.subTest(command=command):
                self.assertIsNone(self.verdict(command))
        for command in [
            "ACTIONS_RUN_REASON='' gh workflow run build.yml",
            "ACTIONS_RUN_REASON='   ' gh workflow run build.yml",
            'ACTIONS_RUN_REASON=1 gh workflow run build.yml',
            'ACTIONS_RUN_REASON="$REASON" gh workflow run build.yml',
            "ACTIONS_RUN_REASON='required recovery' true; gh workflow run build.yml",
            "echo ACTIONS_RUN_REASON=reason; gh run rerun 123",
            "ACTIONS_RUN_REASON='required recovery' gh workflow run a; gh workflow run b",
        ]:
            with self.subTest(command=command):
                self.assertTrue(self.verdict(command))

    def test_pr_checks_watch_requires_at_least_sixty_seconds(self):
        for command in ['gh pr checks 12 --watch', 'gh pr checks --watch -i 10',
                        'gh pr checks --watch --interval=30',
                        'gh -R owner/repo pr checks --watch -i0',
                        'timeout 30 gh pr checks --watch --interval 59',
                        'gh pr checks --watch -i070',
                        'gh pr checks --watch --interval=0060',
                        'gh pr checks --watch --interval=0_70',
                        'gh pr checks --watch --interval=+070',
                        'gh pr checks --watch --interval=0x3b',
                        'gh pr checks --watch --interval=0o73',
                        'gh pr checks --watch --interval=0b111011',
                        'bash -lc "gh pr checks 12 --watch"',
                        "ACTIONS_RUN_REASON='required recovery' gh pr checks --watch"]:
            with self.subTest(command=command):
                self.assertTrue(self.verdict(command))
        for command in ['gh pr checks 12', 'gh pr checks --watch --interval 60',
                        'gh pr checks --watch -i60', 'gh pr checks --watch --interval=120',
                        'gh pr checks --watch -i075', 'gh pr checks --watch --interval=0x3c',
                        'gh pr checks --watch --interval=0o74',
                        'gh pr checks --watch --interval=0b111100',
                        'gh pr checks --watch --help', "echo 'gh pr checks --watch'",
                        'gh pr checks --watch=false']:
            with self.subTest(command=command):
                self.assertIsNone(self.verdict(command))

    def test_rest_polling_loop_is_denied_with_alternatives(self):
        for command in [
            'for i in $(seq 1 19); do out=$(gh api repos/o/r/actions/runs/1 --jq .status); sleep 30; done',
            'while true; do gh run view 1 --json status; sleep 25; done',
            'until gh run view 1 --json status | grep -q completed; do sleep 10; done',
            'bash -c "while :; do gh api repos/o/r/pulls/1; sleep 5; done"',
            'while sleep 30; do gh -R o/r run list; done',
            'for i in 1 2 3; do gh api repos/o/r/actions/runs/1; sleep "$INTERVAL"; done',
            'watch -n 25 gh api repos/o/r/actions/runs/1',
            'watch gh run view 1',
            "bash <<'EOF'\nwhile :; do gh api repos/o/r; sleep 20; done\nEOF",
        ]:
            with self.subTest(command=command):
                reason = self.verdict(command) or ''
                self.assertIn('gh pr checks --watch --interval 60', reason, command)
                self.assertIn('GraphQL', reason)

    def test_single_reads_batches_slow_loops_and_graphql_pass(self):
        for command in [
            'gh api repos/o/r/actions/runs/1', 'gh run view 1 --log', 'sleep 5; gh api repos/o/r',
            'for r in a b c; do gh api repos/o/$r; done',
            'while :; do gh api repos/o/r/actions/runs/1; sleep 60; done',
            'while :; do gh run view 1; sleep 2m; done',
            'while :; do s=$(gh run view 1); case "$s" in done) ACTIONS_RUN_REASON="rerun lost job" gh run rerun 1; sleep 15; exit;; esac; sleep 60; done',
            'while :; do gh api graphql -f query=q; sleep 5; done',
            'while :; do gh api rate_limit; sleep 5; done',
            'while :; do gh pr view 1 --json state; sleep 5; done',
            'for f in a b; do sleep 1; done; gh --version',
            "echo 'while :; do gh api repos/o/r; sleep 5; done'",
            "cat <<'EOF' > x.sh\nwhile :; do gh api repos/o/r; sleep 5; done\nEOF",
        ]:
            with self.subTest(command=command):
                self.assertIsNone(self.verdict(command), command)

    def test_loop_rule_judges_only_what_one_loop_contains(self):
        """A loop elsewhere in the command must not turn a single read into polling."""
        for command in [
            'for f in a b; do echo $f; done; sleep 2; gh api repos/o/r',
            'sleep 15; for r in 1 2; do gh run view $r -R o/r --json status; done',
            'until false; do break; done; gh run view 1 -R o/r; sleep 45; gh run view 1 -R o/r',
            'sleep $(( 3 )) && for j in a b c; do gh api repos/o/r/actions/jobs/$j/logs; done',
            'for id in $(gh run list --json databaseId); do echo $id; sleep 1; done',
            'for f in a b; do sleep 1; done # settle\ngh api repos/o/r',
            'for f in a b; do\n  echo $f # name\ndone\nsleep 2\ngh api repos/o/r',
            'gh api repos/o/r; while :; do sleep 5; break; done',
            'for r in a b; do gh api repos/o/$r; done; for f in a b; do sleep 1; done',
            'while :; do gh run view 1; if x; then sleep 5; exit; else sleep 60; fi; done',
        ]:
            with self.subTest(command=command):
                self.assertIsNone(self.verdict(command), command)
        for command in [
            'sleep 90; while :; do gh api repos/o/r; sleep 5; done',
            'while :; do for r in a b; do gh api repos/o/$r; done; sleep 5; done',
            'while :; do gh api repos/o/r; for i in 1 2; do sleep 5; done; done',
            'while [ "$(gh run view 1 --json status)" != done ]; do sleep 5; done',
            'while :; do s="`gh run view 1`"; sleep 5; done',
            'while :; do bash -c "gh api repos/o/r"; sleep 5; done',
            'while :; do\n  gh api repos/o/r # poll\n  sleep 5 # short\ndone',
            'while :; do s=$(gh run view 1); case $s in\n done) break;;\n esac; sleep 5; done',
            '(while :; do gh api repos/o/r; sleep 5; done)',
        ]:
            with self.subTest(command=command):
                self.assertIn('gh pr checks --watch --interval 60', self.verdict(command) or '', command)

    def budget_verdict(self, command, home, session='s1'):
        with unittest.mock.patch.dict(os.environ, {'HOME': home}):
            return COMMON.riders({'tool_name': 'Bash', 'session_id': session, 'tool_input': {'command': command}})

    def test_one_session_cannot_spend_the_shared_rest_quota(self):
        """Hundreds of one-call-per-command log downloads must stop before they spend the whole quota."""
        read = 'gh api repos/o/r/actions/jobs/1/logs'
        with tempfile.TemporaryDirectory() as home:
            for number in range(300):
                self.assertIsNone(self.budget_verdict(read, home), number)
            reason = self.budget_verdict(read, home) or ''
            self.assertIn('GH_REST_REASON', reason)
            self.assertIn('300', reason)
            self.assertTrue(self.budget_verdict('gh run view 1', home))
            self.assertTrue(self.budget_verdict("GH_REST_REASON=x " + read, home))
            self.assertIsNone(self.budget_verdict("GH_REST_REASON='needed for incident triage' " + read, home))
            for free in ['gh pr view 1', 'gh api graphql -f query=q', 'gh api rate_limit', 'gh api --help', 'ls']:
                self.assertIsNone(self.budget_verdict(free, home), free)
            self.assertIsNone(self.budget_verdict(read, home, session='s2'))

    def test_budget_counts_each_call_in_a_command_and_forgets_after_an_hour(self):
        three = 'gh api repos/o/a; gh api -X GET repos/o/b; x=$(gh run list)'
        with tempfile.TemporaryDirectory() as home:
            for _ in range(100):
                self.assertIsNone(self.budget_verdict(three, home))
            self.assertTrue(self.budget_verdict('gh api repos/o/a', home))
            state = Path(home) / '.local/state/gh-rest-budget/s1'
            state.write_text(''.join(f'{int(time.time()) - 3601}\n' for _ in range(300)))
            self.assertIsNone(self.budget_verdict('gh api repos/o/a', home))
            self.assertEqual(len(state.read_text().split()), 1)
            for count, command in [(1, 'bash -c "echo $(gh api repos/o/a)"'),
                                   (2, 'x=$(gh api repos/o/a); y=$(gh api repos/o/a)')]:
                state.write_text('')
                self.assertIsNone(self.budget_verdict(command, home))
                self.assertEqual(len(state.read_text().split()), count, command)

    def test_unwritable_budget_state_never_blocks(self):
        with tempfile.NamedTemporaryFile() as not_a_directory:
            self.assertIsNone(self.budget_verdict('gh api repos/o/a', not_a_directory.name))
    REASONED = "ACTIONS_RUN_REASON='required recovery' "

    def rerun_verdict(self, command):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'log'
            with unittest.mock.patch.dict(os.environ, {'FAKE_GH_LOG': str(log)}):
                reason = COMMON.riders({'tool_name': 'Bash', 'cwd': directory, 'tool_input': {'command': command}})
            return reason, [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def test_rerun_of_an_older_commit_of_an_open_pr_is_denied(self):
        for command in [
            'gh run rerun 111 -R owner/repo --failed 2>&1',
            'gh -Rowner/repo run rerun --failed 111',
            'gh run rerun --job 9 --repo=owner/repo',
            'gh run rerun -R owner/repo --job=9 2>&1 | tail -3',
            'gh api -X POST repos/owner/repo/actions/runs/111/rerun-failed-jobs',
            'gh api -X POST repos/owner/repo/actions/jobs/9/rerun',
            'if true; then ' + self.REASONED + 'gh run rerun 111 -R owner/repo; fi',
            'gh run rerun 222 -R owner/repo; ' + self.REASONED + 'gh run rerun 111 -R owner/repo',
        ]:
            with self.subTest(command=command):
                reason, calls = self.rerun_verdict(self.REASONED + command if command.startswith('gh') else command)
                self.assertIn('#7', reason or '', command)
                self.assertIn('new', reason)
                self.assertTrue(all(repo == 'owner/repo' for repo, _ in calls), calls)

    def test_rerun_of_the_current_head_a_push_run_or_a_closed_branch_passes(self):
        for command in ['gh run rerun 222 -R owner/repo --failed', 'gh run rerun 333 -R owner/repo',
                        'gh run rerun 444 -R owner/repo', 'gh run rerun 222', 'gh run rerun --help']:
            with self.subTest(command=command):
                self.assertIsNone(self.rerun_verdict(self.REASONED + command)[0])

    def test_rerun_that_cannot_be_verified_is_denied_within_the_hook_timeout(self):
        for command in ['gh run rerun "$RUN" -R owner/repo', 'gh run rerun -R owner/repo',
                        'gh run rerun 999 -R owner/repo', 'gh run rerun 555 -R owner/repo']:
            with self.subTest(command=command):
                started = time.monotonic()
                reason = self.rerun_verdict(self.REASONED + command)[0] or ''
                self.assertIn('-R OWNER/REPO', reason, command)
                self.assertLess(time.monotonic() - started, 7)  # the registered hook is cut at 10 s

    def test_rerun_without_a_reason_is_denied_before_any_lookup(self):
        reason, calls = self.rerun_verdict('gh run rerun 111 -R owner/repo')
        self.assertIn('ACTIONS_RUN_REASON', reason)
        self.assertEqual(calls, [])

    def test_both_runtime_payloads_receive_native_deny_and_allow(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith(('HERDR_', 'MUNICE_'))}
        env.pop('ACTIONS_RUN_REASON', None)
        for runtime in ('claude', 'codex'):
            for command, denied in [('gh workflow run build.yml', True),
                                    ("ACTIONS_RUN_REASON='required recovery' gh workflow run build.yml", False),
                                    ('gh run watch 123 --interval 3', True),
                                    ('timeout 30 gh run watch 123', True),
                                    ('gh pr checks 12 --watch', True),
                                    ('gh pr checks 12 --watch --interval 60', False),
                                    ('gh pr checks 12 --watch --interval 0060', True),
                                    ('gh pr checks 12 --watch --interval 0x3c', False),
                                    ('uv run --no-project python3 ~/.dotfiles/tools/agent-hooks/ci-watch.py --repo owner/repo 123', False),
                                    ('while :; do gh api repos/o/r; sleep 5; done', True),
                                    ('git push origin feature', False)]:
                with self.subTest(runtime=runtime, command=command):
                    payload = {'hook_event_name': 'PreToolUse', 'tool_name': 'Bash',
                               'tool_input': {'command': command}, 'session_id': runtime + '-fixture'}
                    result = subprocess.run([sys.executable, '-I', str(ENTRY)], input=json.dumps(payload),
                                            text=True, capture_output=True, env=env, timeout=12)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    if denied:
                        decision = json.loads(result.stdout)['hookSpecificOutput']
                        self.assertEqual(decision['permissionDecision'], 'deny')
                    else:
                        self.assertEqual(result.stdout, '')


if __name__ == '__main__':
    unittest.main()
