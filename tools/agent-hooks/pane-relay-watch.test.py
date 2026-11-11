#!/usr/bin/env python3
"""Exercise stalled queues without contacting a real pane; --selftest runs all cases."""
import fcntl
import importlib.util
import os
from pathlib import Path
import tempfile
import sys
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("relay_watch", HERE / "pane-relay-watch.py")
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)
NOTIFY = watch.notify


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class QueueWatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = {"HOME": self.tmp.name, "XDG_STATE_HOME": self.tmp.name}
        self.root = Path(self.tmp.name) / "pane-tell"
        self.alert = mock.patch.object(watch, "notify", return_value=True).start()
        self.addCleanup(mock.patch.stopall)

    def payload(self, relative, age=601):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("PRIVATE PAYLOAD: never in an alert")
        os.utime(path, (1000 - age, 1000 - age))
        return path

    def test_young_queue_and_empty_lock_are_not_stalls(self):
        self.payload("waiting/new", 599)
        self.payload("sending/pane.lock")
        watch.tick(self.env, 1000)
        self.alert.assert_not_called()

    def test_waiting_alert_deduplicates_without_opening_message(self):
        path = self.payload("waiting/old")
        with mock.patch.object(Path, "read_text", side_effect=AssertionError("payload read")):
            watch.tick(self.env, 1000)
            watch.tick(self.env, 1100)
        self.assertEqual(self.alert.call_count, 1)
        self.assertIn(str(path), self.alert.call_args.args[-1])
        self.assertNotIn("PRIVATE", self.alert.call_args.args[-1])

    def test_all_batch_stages_are_watched(self):
        for stage in ("open/123.1", "sending.1.2/124.1", ".joined.abc", ".new.def"):
            with self.subTest(stage=stage):
                path = self.payload("batch/term_test/" + stage)
                watch.tick(self.env, 1000)
                self.assertIn(str(path), self.alert.call_args.args[-1])
                path.unlink()

    def test_failed_notification_retries(self):
        self.payload("waiting/old")
        self.alert.side_effect = [False, True, True]
        watch.tick(self.env, 1000)
        watch.tick(self.env, 1100)
        watch.tick(self.env, 1200)
        self.assertEqual(self.alert.call_count, 2)

    def test_undelivered_payload_is_alerted_after_sender_dies(self):
        path = self.payload("undelivered/123.abcdef")
        watch.tick(self.env, 1000)
        self.assertIn(str(path), self.alert.call_args.args[-1])
        self.assertTrue(path.exists())

    def test_caller_failure_is_saved_before_alert_and_retried(self):
        self.alert.side_effect = [False, True]
        watch.failed(self.env, "default/w1:p1", "/private/report.md", now=1000)
        records = list((self.root / "failed-reports").glob("report.*"))
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].stat().st_mode & 0o777, 0o600)
        self.assertIn("/private/report.md", records[0].read_text())
        watch.tick(self.env, 1001)
        watch.tick(self.env, 1002)
        self.assertEqual(self.alert.call_count, 2)
        self.assertIn("/private/report.md", self.alert.call_args.args[-1])

    def test_delivered_file_is_not_touched_and_reused_name_alerts(self):
        path = self.payload("waiting/old")
        watch.tick(self.env, 1000)
        self.assertTrue(path.exists())
        path.unlink()
        watch.tick(self.env, 1100)
        self.payload("waiting/old", 700)
        watch.tick(self.env, 1200)
        self.assertEqual(self.alert.call_count, 2)

    def test_real_notify_nonzero_and_timeout_are_failures(self):
        with mock.patch.object(watch.subprocess, "run", return_value=mock.Mock(returncode=3)):
            self.assertFalse(NOTIFY({"HERDR_ERROR_NOTIFY": "/fake"}, "title", "body"))
        with mock.patch.object(watch.subprocess, "run", side_effect=watch.subprocess.TimeoutExpired("fake", 15)):
            self.assertFalse(NOTIFY({"HERDR_ERROR_NOTIFY": "/fake"}, "title", "body"))

    def test_overlapping_tick_does_not_duplicate_alert(self):
        self.payload("waiting/old")
        with open(self.root / ".watch.lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(watch.tick(self.env, 1000), [])
        self.alert.assert_not_called()
        watch.tick(self.env, 1000)
        self.alert.assert_called_once()

    def test_queue_watch_runs_when_herdr_is_unavailable(self):
        workers = load("workers_watch_test", "workers-watch.py")
        path = self.payload("waiting/old")
        with mock.patch.object(workers.herdr_peers, "all_panes", return_value=None), \
                mock.patch.object(workers.atc.relay_watch, "notify", return_value=True) as alert:
            self.assertEqual(workers.tick(self.env, 1000), [])
        alert.assert_called_once()
        self.assertIn(str(path), alert.call_args.args[-1])

    def test_failed_caller_alert_survives_missing_main_fallback(self):
        atc = load("auto_tab_close_test", "auto-tab-close.py")
        me = {"pane_id": "w1:p2", "terminal_id": "worker", "label": "test"}
        replacements = {
            "here": me, "herdr": {"panes": [], "agents": []}, "hidden_agents": [],
            "dispatch_job": False, "should_close": False, "save": "/private/report.md",
            "moving": False, "lead_pane": ("default/w1:p1", {"terminal_id": "lead"}),
            "main_pane": ("", {}), "route_main_report": None,
        }
        from contextlib import ExitStack
        with ExitStack() as stack:
            for name, result in replacements.items():
                stack.enter_context(mock.patch.object(atc, name, return_value=result))
            stack.enter_context(mock.patch.object(atc.herdr_peers, "server_key", return_value="workers"))
            stack.enter_context(mock.patch.object(atc.herdr_peers, "main_terminal", return_value="main"))
            stack.enter_context(mock.patch.object(atc.herdr_peers, "remote_pane", return_value=False))
            stack.enter_context(mock.patch.object(atc.subprocess, "run", return_value=mock.Mock(returncode=3)))
            alert = stack.enter_context(mock.patch.object(atc.relay_watch, "notify", return_value=True))
            atc.finish("report", self.env)
        alert.assert_called_once()
        self.assertIn("default/w1:p1", alert.call_args.args[-1])
        self.assertIn("/private/report.md", alert.call_args.args[-1])


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.argv.remove("--selftest")
    unittest.main()
