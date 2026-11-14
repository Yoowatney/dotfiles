#!/usr/bin/env python3
"""All commands are inert hook inputs: never start an Actions run to test a guard."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
ENTRY = ROOT / 'tools/claude/hooks/gcp-account-guard.py'
SPEC = importlib.util.spec_from_file_location('common_guard', ENTRY)
COMMON = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COMMON)


class ActionsGuardTest(unittest.TestCase):
    def verdict(self, command, tool='Bash'):
        return COMMON.riders({'tool_name': tool, 'tool_input': {'command': command}})

    def test_manual_execution_is_denied_at_existing_common_entry(self):
        commands = [
            'gh workflow run build.yml', 'gh run rerun 123 --failed',
            'gh workflow enable build.yml', '/opt/homebrew/bin/gh workflow run build.yml',
            'gh -R owner/repo workflow run build.yml', 'gh --repo=owner/repo run rerun 123',
            'gh workflow --repo owner/repo run build.yml',
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
            'gh run watch 123', 'gh workflow list', 'gh workflow view build.yml',
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

    def test_both_runtime_payloads_receive_native_deny_and_allow(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith(('HERDR_', 'MUNICE_'))}
        env.pop('ACTIONS_RUN_REASON', None)
        for runtime in ('claude', 'codex'):
            for command, denied in [('gh workflow run build.yml', True),
                                    ("ACTIONS_RUN_REASON='required recovery' gh workflow run build.yml", False),
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
