#!/usr/bin/env python3
"""Hook boundaries use an isolated inbox double; no live herdr or user state."""
import json
import importlib.util
import os
from pathlib import Path
import shutil
import shlex
import signal
import subprocess
import tempfile
import unittest


HERE = Path(__file__).resolve().parent


class MainAlertHooksTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="main-alert-hooks-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.hooks = self.home / "hooks"
        self.hooks.mkdir()
        for name in ("pane-label-nudge.sh", "yoyoo-share-guard.py", "herdr_peers.py", "correction-nudge.py"):
            shutil.copy2(HERE / name, self.hooks / name)
        (self.hooks / "pane-label").write_text('printf "%s\\n" "$*" > "$HOME/rename"\n')
        self.herdr = self.home / "herdr"
        self.herdr.write_text('''#!/usr/bin/env python3
import json, os, sys, time
if sys.argv[1:3] == ["pane", "get"]:
    if os.environ.get("QUERY_SLEEP"): time.sleep(3)
    if os.environ.get("QUERY_FAIL"):
        print(os.environ["PANE_JSON"])
        sys.exit(1)
    print(os.environ["PANE_JSON"])
elif sys.argv[1:] == ["pane", "list"]:
    print('{"result":{"panes":[]}}')
else:
    sys.exit(2)
''')
        self.herdr.chmod(0o755)
        # The reader's storage/ack contract is tested by its own suite. This
        # boundary double catches wrong event/session forwarding and ack calls.
        (self.hooks / "main-alert-inbox.py").write_text('''import json, os, sys
def drain(env, terminal_id, session_id, hook_event, now=None):
    with open(env["HOME"] + "/drains", "a") as f:
        f.write(json.dumps([terminal_id, session_id, hook_event]) + "\\n")
    if env.get("INBOX_FAIL"):
        raise RuntimeError("inbox unavailable")
    if not env.get("INBOX_EMPTY"):
        return {"hookSpecificOutput": {"hookEventName": hook_event,
            "additionalContext": "CLAIM alert-1; ack: main-alert-inbox.py ack --token claim-1"}}
def ack(*args, **kwargs):
    raise AssertionError("hook output is not an acknowledgment")
if __name__ == "__main__":
    args = sys.argv[1:]
    assert args.pop(0) == "drain"
    flags = dict(zip(args[::2], args[1::2]))
    result = drain(os.environ, flags["--terminal"], flags["--session"], flags["--hook-event"])
    if result: print(json.dumps(result))
''')
        state = self.home / "state" / "orchestrator"
        state.mkdir(parents=True)
        (state / "main").write_text("term-main\n")
        self.env = {**os.environ, "HOME": str(self.home), "XDG_STATE_HOME": str(self.home / "state"),
                    "HERDR_PANE_ID": "w1:p1", "HERDR_BIN_PATH": str(self.herdr)}
        self.env.pop("CLAUDE_CODE_ENTRYPOINT", None)
        self.env.pop("HERDR_SOCKET_PATH", None)
        self.pane = {"pane_id": "w1:p1", "terminal_id": "term-main", "label": "Main",
                     "agent_session": {"value": "session-1"}}

    def run_hook(self, event="UserPromptSubmit", *, claude=False, bound=12, **updates):
        payload = {"hook_event_name": event, "session_id": "session-1", "cwd": "/work/project",
                   "prompt": "hello", "last_assistant_message": "Finished."}
        payload.update(updates)
        env = {**self.env, "PANE_JSON": json.dumps({"result": {"pane": self.pane}})}
        if event == "Stop":
            command = ["python3", "-I", str(self.hooks / "yoyoo-share-guard.py")]
            if claude: command.append("--claude")
        else:
            command = ["bash", str(self.hooks / "pane-label-nudge.sh")]
            if event == "SessionStart": command.append("session")
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, env=env, start_new_session=True)
        try:
            stdout, stderr = process.communicate(json.dumps(payload), timeout=bound)
        except subprocess.TimeoutExpired:
            # A blocked shell can leave its cat child holding these test pipes.
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise
        self.assertEqual(process.returncode, 0, stderr)
        return json.loads(stdout) if stdout.strip() else None

    def context(self, result):
        return result.get("reason", "") if "reason" in result else result["hookSpecificOutput"]["additionalContext"]

    def calls(self):
        path = self.home / "drains"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_prompt_preserves_label_context_and_offers_claim_without_ack(self):
        result = self.run_hook()
        self.assertIn("main pane", self.context(result))
        self.assertIn("ack: main-alert-inbox.py ack", self.context(result))
        self.assertEqual(self.calls(), [["term-main", "session-1", "UserPromptSubmit"]])

    def test_session_start_emits_correct_schema_and_keeps_named_pane(self):
        result = self.run_hook("SessionStart")
        self.assertEqual(result["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIn("CLAIM alert-1", self.context(result))
        self.assertFalse((self.home / "rename").exists())

    def test_session_start_still_names_unnamed_pane(self):
        self.pane["label"] = ""
        self.run_hook("SessionStart")
        self.assertEqual((self.home / "rename").read_text().strip(), "새 세션 project")
        self.assertEqual(len(self.calls()), 1)

    def test_main_fifo_never_blocks_or_offers_in_any_hook(self):
        main = self.home / "state/orchestrator/main"
        main.unlink()
        os.mkfifo(main)
        for event in ("SessionStart", "UserPromptSubmit", "Stop"):
            with self.subTest(event=event):
                result = self.run_hook(event, bound=2)
                self.assertEqual(self.calls(), [])
                self.assertTrue(result is None or "CLAIM" not in self.context(result))
        self.assertFalse((self.home / "rename").exists(), "named SessionStart must keep its label")

    def test_malformed_main_never_authorizes_matching_snapshot(self):
        main = self.home / "state/orchestrator/main"
        for invalid in ("term-main\nother", "x" * 300, "term-main\x00"):
            with self.subTest(registration=repr(invalid)):
                main.write_text(invalid)
                self.pane["terminal_id"] = invalid
                self.assertIsNone(self.run_hook("SessionStart", bound=2))
                self.assertEqual(self.calls(), [])
        self.assertFalse((self.home / "rename").exists())

    def test_canonical_main_reader_rejects_unsafe_registration(self):
        main = self.home / "state/orchestrator/main"
        command = ["python3", "-I", str(self.hooks / "herdr_peers.py"), "--delegates-checks", "term-main"]
        for invalid in (b"\xff", b"term-main\x00", b"term-main\nother", b"x" * 257):
            with self.subTest(data=repr(invalid)):
                main.write_bytes(invalid)
                result = subprocess.run(command, env=self.env, capture_output=True, timeout=2)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, b"")
        main.unlink()
        target = self.home / "registration-target"
        target.write_text("term-main\n")
        for kind in ("symlink", "directory", "fifo"):
            with self.subTest(kind=kind):
                if kind == "symlink": main.symlink_to(target)
                elif kind == "directory": main.mkdir()
                else: os.mkfifo(main)
                result = subprocess.run(command, env=self.env, capture_output=True, timeout=2)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, b"")
                main.rmdir() if kind == "directory" else main.unlink()
        for valid in ("term-main\n", " term-main \n", "term_65f2", "legacy.id:1-2"):
            with self.subTest(valid=valid):
                main.write_text(valid)
                result = subprocess.run(command[:-1] + [valid.strip()], env=self.env, capture_output=True, timeout=2)
                self.assertEqual(result.returncode, 0)

    def test_stop_preserves_existing_reason_for_both_clients(self):
        for claude in (False, True):
            with self.subTest(claude=claude):
                result = self.run_hook("Stop", claude=claude, last_assistant_message="머지할까요?")
                self.assertIn("CLAIM alert-1", self.context(result))
                self.assertIn("다시", self.context(result))
                if claude:
                    self.assertEqual(result["hookSpecificOutput"]["hookEventName"], "Stop")
                else:
                    self.assertEqual(result["decision"], "block")
                    self.assertEqual(set(result), {"decision", "reason"})

    def test_stop_offers_claim_when_no_other_reason(self):
        self.assertIn("CLAIM alert-1", self.context(self.run_hook("Stop")))

    def test_active_stop_does_not_offer_or_continue(self):
        self.assertIsNone(self.run_hook("Stop", stop_hook_active=True))
        self.assertEqual(self.calls(), [])

    def test_unverified_identity_never_reaches_drain(self):
        for event in ("UserPromptSubmit", "SessionStart", "Stop"):
            for invalid in ("missing-session", "other-session", "missing-terminal", "other-terminal", "query-fail", "unknown-pane"):
                with self.subTest(event=event, invalid=invalid):
                    original = dict(self.pane)
                    updates = {}
                    if invalid == "missing-session": updates["session_id"] = None
                    if invalid == "other-session": self.pane["agent_session"] = {"value": "another"}
                    if invalid == "missing-terminal": self.pane.pop("terminal_id", None)
                    if invalid == "other-terminal": self.pane["terminal_id"] = "term-other"
                    if invalid == "unknown-pane": self.pane = {}
                    if invalid == "query-fail": self.env["QUERY_FAIL"] = "1"
                    self.run_hook(event, **updates)
                    self.assertEqual(self.calls(), [])
                    self.pane = original
                    self.env.pop("QUERY_FAIL", None)

    def test_inbox_failure_keeps_existing_stop_reason(self):
        self.env["INBOX_FAIL"] = "1"
        result = self.run_hook("Stop", last_assistant_message="머지할까요?")
        self.assertIsNotNone(result)
        self.assertNotIn("CLAIM", self.context(result))

    def test_bounded_query_does_not_claim_late_pane_response(self):
        self.env["QUERY_SLEEP"] = "1"
        self.run_hook()
        self.assertEqual(self.calls(), [])

    def test_empty_inbox_keeps_session_start_silent(self):
        self.env["INBOX_EMPTY"] = "1"
        self.assertIsNone(self.run_hook("SessionStart"))

    def real_inbox(self):
        path = self.hooks / "main-alert-inbox.py"
        shutil.copy2(HERE / path.name, path)
        spec = importlib.util.spec_from_file_location("integration_inbox", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_real_queue_all_hook_events_require_explicit_ack(self):
        inbox = self.real_inbox()
        for event, claude in (("UserPromptSubmit", False), ("SessionStart", False), ("Stop", False), ("Stop", True)):
            with self.subTest(event=event, claude=claude):
                event_id = event + ("-claude" if claude else "-default")
                inbox.enqueue(self.env, event_id, "Recovery fixture", "Private fixture; do not relay.")
                first = self.run_hook(event, claude=claude)
                context = self.context(first)
                self.assertIn('"event_id": "' + event_id + '"', context)
                self.assertEqual(len(inbox.pending(self.env)), 1, "offering context must not consume")
                second = self.run_hook(event, claude=claude)
                self.assertTrue(second is None or "local-main-alert" not in self.context(second), "lease suppresses immediate duplicate")
                command = context.split("Receipt command: ", 1)[1].splitlines()[0]
                receipt = subprocess.run(shlex.split(command), env=self.env, capture_output=True, text=True, timeout=5)
                self.assertEqual(receipt.returncode, 0, receipt.stderr)
                self.assertEqual(inbox.pending(self.env), [])

    def test_real_queue_invalid_identity_never_claims(self):
        inbox = self.real_inbox()
        inbox.enqueue(self.env, "untouched", "Recovery fixture", "Private fixture")
        for event in ("UserPromptSubmit", "SessionStart", "Stop"):
            for invalid in ("missing-session", "other-session", "missing-terminal", "other-terminal", "query-fail", "unknown-pane", "no-herdr", "no-main"):
                with self.subTest(event=event, invalid=invalid):
                    original = dict(self.pane)
                    updates = {}
                    if invalid == "missing-session": updates["session_id"] = None
                    if invalid == "other-session": self.pane["agent_session"] = {"value": "another"}
                    if invalid == "missing-terminal": self.pane.pop("terminal_id", None)
                    if invalid == "other-terminal": self.pane["terminal_id"] = "term-other"
                    if invalid == "unknown-pane": self.pane = {}
                    if invalid == "query-fail": self.env["QUERY_FAIL"] = "1"
                    if invalid == "no-herdr": self.env.pop("HERDR_PANE_ID", None)
                    main = self.home / "state/orchestrator/main"
                    if invalid == "no-main": main.unlink()
                    self.run_hook(event, **updates)
                    state = json.loads((self.home / "state/orchestrator/alerts/state.json").read_text())
                    self.assertIsNone(state["claim"])
                    self.assertEqual(len(inbox.pending(self.env)), 1)
                    self.pane = original
                    self.env.pop("QUERY_FAIL", None)
                    self.env["HERDR_PANE_ID"] = "w1:p1"
                    main.write_text("term-main\n")

    def test_real_queue_active_stop_and_sdk_leave_no_claim(self):
        inbox = self.real_inbox()
        inbox.enqueue(self.env, "held", "Recovery fixture", "Private fixture")
        self.assertIsNone(self.run_hook("Stop", stop_hook_active=True))
        self.env["CLAUDE_CODE_ENTRYPOINT"] = "sdk-cli"
        self.assertIsNone(self.run_hook("Stop"))
        state = json.loads((self.home / "state/orchestrator/alerts/state.json").read_text())
        self.assertIsNone(state["claim"])
        self.assertEqual(len(inbox.pending(self.env)), 1)

    def test_real_queue_new_runtime_gets_unacked_claim_immediately(self):
        inbox = self.real_inbox()
        inbox.enqueue(self.env, "restart", "Recovery fixture", "Private fixture")
        first = self.context(self.run_hook("UserPromptSubmit"))
        old_command = first.split("Receipt command: ", 1)[1].splitlines()[0]
        self.pane["agent_session"] = {"value": "session-2"}
        second = self.context(self.run_hook("SessionStart", session_id="session-2"))
        self.assertIn('"event_id": "restart"', second)
        self.assertIn("--session session-2", second)
        old_ack = subprocess.run(shlex.split(old_command), env=self.env, capture_output=True, text=True, timeout=5)
        self.assertNotEqual(old_ack.returncode, 0)
        self.assertEqual(len(inbox.pending(self.env)), 1)

    def test_large_prompt_snapshot_and_context_use_normal_input_streams(self):
        inbox = self.real_inbox()
        inbox.enqueue(self.env, "large-stream", "Recovery fixture", "가" * 900)
        self.pane["fixture_metadata"] = "large snapshot " * 1000
        result = self.run_hook("UserPromptSubmit", prompt="hello\n" * 3000)
        context = self.context(result)
        self.assertIn('"event_id": "large-stream"', context)
        self.assertIn("가" * 900, context)
        self.assertIn("main pane", context)
        self.assertEqual(len(inbox.pending(self.env)), 1)


if __name__ == "__main__":
    unittest.main()
