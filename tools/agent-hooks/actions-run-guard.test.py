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
