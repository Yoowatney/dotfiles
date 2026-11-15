#!/usr/bin/env python3
"""Exercise the real Stop hook and a local checker; no live service access."""
import json
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


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
        herdr.write_text("#!" + sys.executable + "\nimport json,os,sys,time\n"
                         "time.sleep(float(os.environ.get('TEST_HERDR_SLEEP','0')))\n"
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
        self.sequence = 0
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
        self.sequence += 1
        payload.setdefault("turn_id", str(self.sequence))
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
                        {"timeout_seconds": 6}, {"timeout_seconds": 21}, {"timeout_seconds": 31}):
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

    def test_failure_limit_is_durable_per_turn_and_preserves_other_reasons(self):
        for mode in ("failure", "invalid", "missing", "timeout"):
            with self.subTest(mode=mode):
                self.policy()
                extra = {"TEST_EXIT": "1"}
                if mode == "invalid":
                    self.config.write_text("{")
                elif mode == "missing":
                    self.policy(command=[str(self.root / "missing")])
                elif mode == "timeout":
                    extra["TEST_SLEEP"] = "5"
                payload = {"session_id": "test-session", "turn_id": mode,
                           "stop_hook_active": True, "last_assistant_message": "done"}
                for _ in range(3):
                    self.assert_block(self.run_hook(payload=payload, **extra), "reaction-policy")
                self.assertIsNone(self.run_hook(payload=payload, **extra))
                state = next((self.root / "state/agent-hooks/reaction-checks").glob("*.json"))
                record = json.loads(state.read_text())
                self.assertEqual(record["failures"], 3)
                self.assertGreater(record["backoff_until"], time.time() + 290)
        note = self.root / ".local/state/next-prompt/test-session"
        self.assertIn("backoff", note.read_text())

    def test_success_resets_failure_count_and_defer_context_is_preserved(self):
        payload = {"session_id": "test-session", "turn_id": "same-turn", "stop_hook_active": True}
        self.assert_block(self.run_hook(payload=payload, TEST_EXIT="1"), "reaction-policy")
        self.checker.write_text("print('diagnostic')\nprint('{\"additional_context\":\"durable retry scheduled\"}')\n")
        self.assertIsNone(self.run_hook(payload=payload))
        state = next((self.root / "state/agent-hooks/reaction-checks").glob("*.json"))
        self.assertEqual(json.loads(state.read_text())["failures"], 0)
        self.assertIn("durable retry scheduled", (self.root / ".local/state/next-prompt/test-session").read_text())

    def test_state_io_failure_warns_without_looping_or_suppressing_ordinary_block(self):
        state = self.root / "state/agent-hooks"
        state.write_text("cannot create a directory here")
        self.assertIsNone(self.run_hook(TEST_EXIT="1"))
        self.assertIn("state", (self.root / ".local/state/next-prompt/test-session").read_text())
        result = self.run_hook(payload={"session_id": "test-session", "stop_hook_active": False,
                                       "last_assistant_message": "머지할까요?"}, TEST_EXIT="1")
        self.assert_block(result, "yoyoo-share-guard")

    def test_reaction_runs_in_parallel_and_keeps_decide_inbox_order_and_reasons(self):
        spec = importlib.util.spec_from_file_location("reaction_parallel_test", HOOK)
        hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hook)
        order = []
        started = threading.Event()
        def decide(*args):
            order.append("decide")
            self.assertTrue(started.wait(2), "reaction must start before ordinary checks finish")
            time.sleep(.4)
            return "ordinary reason"
        def inbox(*args):
            order.append("inbox")
            time.sleep(.1)
            return "inbox reason"
        def reaction(*args):
            started.set()
            time.sleep(.4)
            return "reaction reason"
        with patch.object(hook, "decide", decide), patch.object(hook, "inbox_context", inbox), patch.object(hook, "reaction_reason", reaction):
            start = time.monotonic()
            result = hook.hook_output({}, {}, True)
        self.assertLess(time.monotonic() - start, .8)
        self.assertEqual(order, ["decide", "inbox"])
        self.assertEqual(result, {"decision": "block", "reason": "inbox reason\n\nordinary reason\n\nreaction reason"})
        with patch.object(hook, "inbox_context", return_value=None), patch.object(hook, "reaction_reason", return_value="reaction reason"):
            self.assertEqual(hook.hook_output({"stop_hook_active": True}, {}, False),
                             {"decision": "block", "reason": "reaction reason"})
        with patch.object(hook, "decide", return_value="ordinary reason"), patch.object(hook, "inbox_context", return_value="inbox reason"), patch.object(hook, "reaction_reason", side_effect=RuntimeError("infrastructure")):
            self.assertEqual(hook.hook_output({}, {}, False),
                             {"decision": "block", "reason": "inbox reason\n\nordinary reason"})

    def test_backoff_does_not_suppress_ordinary_stop_reason(self):
        payload = {"session_id": "test-session", "turn_id": "same-turn", "stop_hook_active": True}
        for _ in range(3):
            self.assert_block(self.run_hook(payload=payload, TEST_EXIT="1"), "reaction-policy")
        payload.update(stop_hook_active=False, last_assistant_message="머지할까요?")
        result = self.run_hook(payload=payload, TEST_EXIT="1")
        self.assert_block(result, "yoyoo-share-guard")
        self.assertNotIn("reaction-policy", result["reason"])

    def test_transcript_turn_key_survives_resume_and_resets_on_new_input(self):
        transcript = self.root / "session.jsonl"
        user = {"type": "user", "uuid": "first", "message": {"content": "same request"}}
        transcript.write_text(json.dumps(user) + "\n")
        payload = {"session_id": "test-session", "turn_id": None, "stop_hook_active": True,
                   "transcript_path": str(transcript)}
        for _ in range(3):
            self.assert_block(self.run_hook(payload=payload, TEST_EXIT="1"), "reaction-policy")
        with transcript.open("a") as stream:
            stream.write(json.dumps({**user, "uuid": "hook-feedback", "isMeta": True}) + "\n")
        self.assertIsNone(self.run_hook(True, payload=payload, TEST_EXIT="1"))
        with transcript.open("a") as stream:
            stream.write(json.dumps({**user, "uuid": "next"}) + "\n")
        self.assert_block(self.run_hook(payload=payload, TEST_EXIT="1"), "reaction-policy")

    def test_slow_identity_and_max_checker_budget_fit_existing_ten_second_fixture(self):
        self.policy(timeout_seconds=5)
        start = time.monotonic()
        self.assert_block(self.run_hook(TEST_SLEEP="6", TEST_HERDR_SLEEP="1.5"), "timed out")
        self.assertLess(time.monotonic() - start, 9.5)


if __name__ == "__main__":
    unittest.main()
