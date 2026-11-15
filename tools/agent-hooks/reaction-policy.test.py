#!/usr/bin/env python3
"""Exercise the real Stop hook and a local checker; no live service access."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


HOOK = Path(__file__).with_name("yoyoo-share-guard.py")


class ReactionPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "config/agent-hooks/reaction-policy.json"
        self.config.parent.mkdir(parents=True)
        state = self.root / "state/orchestrator"
        state.mkdir(parents=True)
        (state / "main").write_text("term_main\n")
        self.pane = self.root / "pane.json"
        self.set_pane()
        herdr = self.root / "herdr"
        herdr.write_text("#!" + sys.executable + "\nimport json,os,sys\n"
                         "pane=json.load(open(os.environ['TEST_PANE']))\n"
                         "print(json.dumps({'result':{'pane':pane}} if sys.argv[1:3]==['pane','get'] else {'result':{}}))\n")
        herdr.chmod(0o700)
        self.received = self.root / "received.json"
        self.checker = self.root / "checker.py"
        self.checker.write_text("import json,os,sys,time\n"
                               "json.dump(json.load(sys.stdin),open(os.environ['TEST_RECEIVED'],'w'))\n"
                               "time.sleep(float(os.environ.get('TEST_SLEEP','0')))\n"
                               "print(os.environ.get('TEST_DETAIL','checker detail'),file=sys.stderr)\n"
                               "sys.exit(int(os.environ.get('TEST_EXIT','0')))\n")
        self.env = {**os.environ, "HOME": str(self.root), "XDG_CONFIG_HOME": str(self.root / "config"),
                    "XDG_STATE_HOME": str(self.root / "state"), "HERDR_BIN_PATH": str(herdr),
                    "HERDR_PANE_ID": "wTest:p1", "HERDR_SOCKET_PATH": "",
                    "CLAUDE_CODE_ENTRYPOINT": "cli", "TEST_PANE": str(self.pane),
                    "TEST_RECEIVED": str(self.received)}
        self.policy()

    def set_pane(self, terminal="term_main", session="test-session"):
        self.pane.write_text(json.dumps({"terminal_id": terminal, "agent_session": {"value": session}}))

    def policy(self, **changes):
        value = {"version": 1, "command": [sys.executable, "-I", str(self.checker)], "timeout_seconds": 1}
        value.update(changes)
        self.config.write_text(json.dumps(value))

    def run_hook(self, claude=False, payload=None, **environment):
        payload = payload or {"hook_event_name": "Stop", "session_id": "test-session",
                              "stop_hook_active": True, "last_assistant_message": "done"}
        result = subprocess.run([sys.executable, "-I", str(HOOK), *(["--claude"] if claude else [])],
                                input=json.dumps(payload), text=True, capture_output=True,
                                env={**self.env, **environment}, timeout=8)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout) if result.stdout.strip() else None

    def assert_block(self, result, fragment):
        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("decision"), "block", result)
        self.assertIn(fragment, result.get("reason", ""))

    def test_success_is_quiet_and_receives_original_payload(self):
        for claude in (False, True):
            with self.subTest(claude=claude):
                self.assertIsNone(self.run_hook(claude))
                self.assertEqual(json.loads(self.received.read_text())["session_id"], "test-session")

    def test_checker_failure_blocks_both_runtimes_even_recursive_stop(self):
        for claude in (False, True):
            for recursive in (False, True):
                with self.subTest(claude=claude, recursive=recursive):
                    payload = {"hook_event_name": "Stop", "session_id": "test-session",
                               "stop_hook_active": recursive, "last_assistant_message": "done"}
                    self.assert_block(self.run_hook(claude, payload, TEST_EXIT="3"), "checker detail")

    def test_fresh_and_resumed_session_payloads_run_checker(self):
        for runtime in ("claude", "codex"):
            for source in ("startup", "resume"):
                with self.subTest(runtime=runtime, source=source):
                    session = runtime + "-" + source
                    self.set_pane(session=session)
                    payload = {"hook_event_name": "Stop", "session_id": session, "source": source,
                               "stop_hook_active": True, "last_assistant_message": "worker delegated"}
                    self.assert_block(self.run_hook(runtime == "claude", payload, TEST_EXIT="1",
                                                    TEST_DETAIL="loading or checked obligation unresolved"), "obligation unresolved")
                    self.assertEqual(json.loads(self.received.read_text()), payload)

    def test_workers_stale_identity_sdk_and_missing_pane_skip_checker(self):
        for terminal, session, extra in (("term_worker", "test-session", {}),
                                         ("term_main", "old-session", {}),
                                         ("term_main", "test-session", {"CLAUDE_CODE_ENTRYPOINT": "sdk-cli"}),
                                         ("term_main", "test-session", {"HERDR_PANE_ID": ""})):
            with self.subTest(terminal=terminal, session=session, extra=extra):
                self.set_pane(terminal, session)
                self.assertIsNone(self.run_hook(TEST_EXIT="1", **extra))
                self.assertFalse(self.received.exists())

    def test_missing_policy_preserves_existing_behavior(self):
        self.config.unlink()
        self.assertIsNone(self.run_hook(TEST_EXIT="1"))
        self.assertFalse(self.received.exists())

    def test_malformed_policy_blocks_only_verified_main(self):
        self.config.write_text("{")
        self.assert_block(self.run_hook(), "policy")
        self.set_pane(terminal="term_worker")
        self.assertIsNone(self.run_hook())

    def test_invalid_schema_and_broken_link_block(self):
        for changes in ({"version": True}, {"version": 2}, {"command": "echo bad"},
                        {"command": []}, {"command": [""]}, {"command": [1]},
                        {"timeout_seconds": True}, {"timeout_seconds": 0},
                        {"timeout_seconds": 21}, {"timeout_seconds": 31}):
            with self.subTest(changes=changes):
                self.policy(**changes)
                self.assert_block(self.run_hook(), "policy")
        self.config.unlink()
        self.config.symlink_to(self.root / "missing-policy")
        self.assert_block(self.run_hook(), "policy")

    def test_checker_timeout_blocks_with_bounded_wait(self):
        start = time.monotonic()
        self.assert_block(self.run_hook(TEST_SLEEP="5"), "timed out")
        self.assertLess(time.monotonic() - start, 4)

    def test_missing_executable_blocks_without_exposing_argv(self):
        self.policy(command=[str(self.root / "private-command")])
        result = self.run_hook()
        self.assert_block(result, "could not start")
        self.assertNotIn("private-command", result["reason"])

    def test_checker_non_utf8_failure_still_blocks_both_runtimes(self):
        self.checker.write_text("import os,sys\nos.write(2,b'\\xff')\nsys.exit(3)\n")
        for claude in (False, True):
            with self.subTest(claude=claude):
                self.assert_block(self.run_hook(claude), "checker exited 3")


if __name__ == "__main__":
    unittest.main()
