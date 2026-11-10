#!/usr/bin/env python3
"""Regression checks for secret file output; run with --selftest.

Only synthetic command strings enter the hook. No secret file is read and no
SSH connection is made by these tests.
"""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest


GUARD = Path(__file__).with_name("secret-print-guard.py")
SPEC = importlib.util.spec_from_file_location("secret_print_guard", GUARD)
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


class SecretFileOutputTests(unittest.TestCase):
    def test_content_readers_are_denied_locally_and_over_ssh(self):
        paths = (
            ".envrc", ".env", ".env.production", ".env.*",
            "/workspace/project/.envrc", "credentials.json",
            "service-account.json", "private.pem", "~/.ssh/id_ed25519",
        )
        for path in paths:
            for reader in ("cat", "head -2", "sed -n '1,8p'", "less"):
                local = f"{reader} {path}"
                for command in (local, "ssh fixture-host " + repr(local)):
                    with self.subTest(command=command):
                        self.assertIsNotNone(guard.bash_problem(command))

    def test_incident_command_shape_is_denied(self):
        command = (
            "cat .envrc\n"
            "sed -n '1,150p' .claude/API.md\n"
            "rg -n 'viewer|readOnly' Infrastructure -g '*.tf' | head -70\n"
            "rg --files /workspace | rg 'use-account.sh$'"
        )
        self.assertIsNotNone(guard.bash_problem(command))

    def test_remote_shell_wrappers_and_ssh_options_are_denied(self):
        for command in (
            'ssh -p 2222 -o BatchMode=yes fixture-host "cat .envrc"',
            'ssh -i /tmp/fixture-key -J fixture-jump fixture-host "head .env"',
            'ssh -- fixture-host "less credentials.json"',
            '/usr/bin/ssh fixture-host "bash -lc \'cat .envrc\'"',
            'ssh fixture-host cat .envrc',
            'ssh fixture-host "true; cat .envrc"',
            "ssh fixture-host 'V=$(cat .envrc); echo $V'",
            "bash -lc 'cat .envrc'",
            "cat .env.example.production",
            "cat .env.example?",
        ):
            with self.subTest(command=command):
                self.assertIsNotNone(guard.bash_problem(command))

    def test_examples_counts_and_existence_remain_allowed(self):
        for command in (
            "cat .env.example", "head .env.sample", "less .env.template",
            "grep -c TOKEN .envrc", "rg --count TOKEN .env.production",
            "grep -q TOKEN credentials.json", "test -f .envrc",
            "[ -e .env ] && echo exists", "ls -l .envrc", "stat .env",
            "source .envrc", "VALUE=$(cat .envrc)",
            'ssh fixture-host "cat .env.example"',
            'ssh -p 2222 fixture-host "grep -c TOKEN .envrc"',
            'ssh fixture-host "test -f .envrc"',
            "ssh fixture-host 'V=$(cat .envrc); echo ${#V}'",
            "V=$(ssh fixture-host 'cat .envrc')",
            "git commit -m 'block ssh fixture-host cat .envrc'",
        ):
            with self.subTest(command=command):
                self.assertIsNone(guard.bash_problem(command))

    def test_read_and_grep_tools_deny_content_but_allow_counts(self):
        for tool, inp, denied in (
            ("Read", {"file_path": "/workspace/.envrc"}, True),
            ("Read", {"file_path": "/workspace/.env.example"}, False),
            ("Grep", {"path": "/workspace/.envrc", "output_mode": "content"}, True),
            ("Grep", {"path": "/workspace/.envrc", "output_mode": "count"}, False),
        ):
            with self.subTest(tool=tool, input=inp):
                self.assertEqual(guard.tool_problem({"tool_name": tool, "tool_input": inp}) is not None, denied)

    def test_hook_protocol_returns_deny_before_any_command_runs(self):
        payload = {"tool_name": "Bash", "tool_input": {"command": "cat /tmp/fixture/.envrc"}}
        result = subprocess.run(
            [sys.executable, "-I", str(GUARD)], input=json.dumps(payload),
            text=True, capture_output=True, check=True,
        )
        response = json.loads(result.stdout or "{}")
        self.assertEqual(response.get("hookSpecificOutput", {}).get("permissionDecision"), "deny")


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
