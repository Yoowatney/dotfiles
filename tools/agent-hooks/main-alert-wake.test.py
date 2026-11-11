#!/usr/bin/env python3
"""Only synthetic panes and temporary state are used by this suite."""
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("wake", HERE / "main-alert-wake.py")
wake = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wake)


class WakeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = {"HOME": self.tmp.name, "XDG_STATE_HOME": self.tmp.name}
        root = Path(self.tmp.name) / "orchestrator"
        root.mkdir()
        (root / "main").write_text("term_MAIN\n")
        self.pane = dict(pane_id="w1:p1", terminal_id="term_MAIN", agent="codex",
                         agent_status="idle", agent_session={"value": "session1"},
                         scroll={"offset_from_bottom": 0})
        self.calls = []
        self.screen = "›\n"
        self.read_count = 0

    def query(self, env, *args):
        self.calls.append(args)
        if args == ("pane", "list"):
            return {"panes": [self.pane]}
        if args[:2] == ("pane", "get"):
            return {"pane": self.pane}
        if args[:2] == ("pane", "read"):
            self.read_count += 1
            return self.screen
        if args[:2] == ("agent", "prompt"):
            return {}
        raise AssertionError(args)

    def run_tick(self, now=1000):
        with mock.patch.object(wake, "query", side_effect=self.query), \
                mock.patch.object(wake.time, "sleep"):
            return wake.tick(self.env, ["event-one"], now=now)

    def prompts(self):
        return [args for args in self.calls if args[:2] == ("agent", "prompt")]

    def test_idle_main_wakes_once_and_retry_keeps_event_id(self):
        self.assertTrue(self.run_tick())
        self.assertEqual(len(self.prompts()), 1)
        self.assertIn("event-one", self.prompts()[0][-1])
        self.assertFalse(self.run_tick(1001))
        self.assertEqual(len(self.prompts()), 1)
        self.assertTrue(self.run_tick(1121))
        self.assertEqual(len(self.prompts()), 2)
        state = json.loads((Path(self.tmp.name) / "orchestrator/alerts/wake.json").read_text())
        self.assertEqual(state["status"], "prompt_accepted")
        self.assertNotIn("consumed", state)

    def test_missing_main_or_wrong_terminal_never_prompts(self):
        self.pane["terminal_id"] = "term_OTHER"
        self.assertFalse(self.run_tick())
        self.assertEqual(self.prompts(), [])

    def test_main_restart_can_retry_without_waiting_for_old_binding(self):
        self.assertTrue(self.run_tick())
        self.pane["agent_session"]["value"] = "session2"
        self.assertTrue(self.run_tick(1001))
        self.assertEqual(len(self.prompts()), 2)

    def test_clock_rollback_does_not_suspend_pending_alert(self):
        self.assertTrue(self.run_tick(1000))
        self.assertTrue(self.run_tick(900))

    def test_draft_dialog_scroll_working_and_missing_identity_wait(self):
        for field, value in (("screen", "› typed draft\n"), ("screen", "confirmation dialog"),
                             ("scroll", {"offset_from_bottom": 1}), ("agent_status", "working"),
                             ("agent_session", {}), ("agent", "unknown")):
            with self.subTest(field=field, value=value):
                original = self.screen if field == "screen" else self.pane[field]
                if field == "screen":
                    self.screen = value
                else:
                    self.pane[field] = value
                self.assertFalse(self.run_tick())
                if field == "screen":
                    self.screen = original
                else:
                    self.pane[field] = original
        self.assertEqual(self.prompts(), [])
        self.assertFalse(any(args[:2] == ("agent", "send-keys") for args in self.calls))

    def test_second_read_detects_new_draft(self):
        original = self.query
        def changing(env, *args):
            if args[:2] == ("pane", "read") and self.read_count:
                self.screen = "› new draft\n"
            return original(env, *args)
        with mock.patch.object(wake, "query", side_effect=changing), mock.patch.object(wake.time, "sleep"):
            self.assertFalse(wake.tick(self.env, ["event-one"], now=1000))
        self.assertEqual(self.prompts(), [])

    def test_shared_sender_lock_prevents_competing_prompt(self):
        path = Path(self.tmp.name) / "pane-tell/sending/w1:p1.lock"
        path.parent.mkdir(parents=True)
        with path.open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertFalse(self.run_tick())
        self.assertEqual(self.prompts(), [])

    def test_timeout_is_unknown_and_retains_retry_backoff(self):
        original = self.query
        def failing(env, *args):
            if args[:2] == ("agent", "prompt"):
                self.calls.append(args)
                raise subprocess.TimeoutExpired("fake", 2)
            return original(env, *args)
        with mock.patch.object(wake, "query", side_effect=failing), mock.patch.object(wake.time, "sleep"):
            self.assertFalse(wake.tick(self.env, ["event-one"], now=1000))
        self.assertFalse(self.run_tick(1001))
        state = json.loads((Path(self.tmp.name) / "orchestrator/alerts/wake.json").read_text())
        self.assertEqual(state["status"], "acceptance_unknown")
        self.assertTrue(self.run_tick(1121))

    def test_default_environment_does_not_reuse_workers_socket(self):
        env = wake.default_env({**self.env, "HERDR_SOCKET_PATH": "/workers/herdr.sock",
                                "HERDR_PANE_ID": "w1:p9", "HERDR_SESSION": "workers"})
        for key in ("HERDR_SOCKET_PATH", "HERDR_PANE_ID", "HERDR_SESSION"):
            self.assertNotIn(key, env)

    def test_missing_scroll_information_is_not_verified_bottom(self):
        self.pane.pop("scroll")
        self.assertFalse(self.run_tick())
        self.assertEqual(self.prompts(), [])

    def test_symlink_alert_directory_never_writes_or_prompts(self):
        target = Path(self.tmp.name) / "elsewhere"
        target.mkdir()
        (Path(self.tmp.name) / "orchestrator/alerts").symlink_to(target, target_is_directory=True)
        self.assertFalse(self.run_tick())
        self.assertEqual(list(target.iterdir()), [])
        self.assertEqual(self.prompts(), [])

    def test_cli_reads_pending_event_id_without_crashing(self):
        spec = importlib.util.spec_from_file_location("inbox_cli_fixture", HERE / "main-alert-inbox.py")
        inbox = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(inbox)
        inbox.enqueue(self.env, "event-cli", "synthetic", "metadata")
        result = subprocess.run([sys.executable, "-I", str(HERE / "main-alert-wake.py")],
                                env={**os.environ, **self.env, "HERDR_BIN_PATH": "/usr/bin/false"},
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_malformed_runtime_shapes_fail_closed(self):
        for field, value in (("agent_session", "invalid"), ("scroll", "invalid")):
            with self.subTest(field=field):
                original = self.pane[field]
                self.pane[field] = value
                self.assertFalse(self.run_tick())
                self.pane[field] = original
        with mock.patch.object(wake, "query", return_value={"panes": ["invalid"]}):
            self.assertFalse(wake.tick(self.env, ["event-one"], now=1000))
        self.assertEqual(self.prompts(), [])


if __name__ == "__main__":
    unittest.main()
