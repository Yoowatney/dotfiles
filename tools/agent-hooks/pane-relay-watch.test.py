#!/usr/bin/env python3
"""Exercise stalled queues without contacting a real pane; --selftest runs all cases."""
import fcntl
import importlib.util
import json
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
        for stage in ("open/123.1", "sending.1.2/124.1", "claimed/125.1", ".joined.abc", ".new.def"):
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

    def test_default_terminal_import_deduplicates_the_main_event_id(self):
        env = {**self.env, "HERDR_ERROR_NOTIFY": "", "PANE_TELL_ALERT_EVENT_ID": "b7855e17-9dc5-42b7-a6a7-b4d419efde80",
               "PANE_TELL_ALERT_CREATED_AT": "2026-11-11T09:00:00+00:00"}
        self.assertTrue(NOTIFY(env, "synthetic", "metadata only"))
        self.assertTrue(NOTIFY(env, "synthetic", "metadata only"))
        state = json.loads((Path(self.tmp.name) / "herdr-error-notify/state.json").read_text())
        self.assertEqual(len(state["pending"]), 1)
        self.assertEqual(state["pending"][0]["id"], env["PANE_TELL_ALERT_EVENT_ID"])
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
            with self.assertRaises(workers.WatchReadError):
                workers.tick(self.env, 1000)
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

    def test_main_alert_survives_terminal_failure_and_deduplicates_ids(self):
        path = self.payload("waiting/old")
        self.alert.side_effect = [False, True]
        with mock.patch.object(watch, "enqueue_main", return_value=True) as main, \
                mock.patch.object(watch, "wake_main"):
            watch.tick(self.env, 1000)
            watch.tick(self.env, 1100)
            watch.tick(self.env, 1200)
        self.assertEqual(main.call_count, 2)  # No repeated import while terminal retry is pending.
        ids = [call.args[1] for call in main.call_args_list]
        self.assertEqual(len(set(ids)), 1)
        self.assertIn(str(path), main.call_args.args[-1])
        self.assertNotIn("PRIVATE", main.call_args.args[-1])
        self.assertEqual(self.alert.call_count, 2)

    def test_main_enqueue_failure_does_not_suppress_terminal_retry(self):
        self.payload("waiting/old")
        with mock.patch.object(watch, "enqueue_main", return_value=False) as main, \
                mock.patch.object(watch, "wake_main"):
            watch.tick(self.env, 1000)
            watch.tick(self.env, 1100)
        self.assertEqual(main.call_count, 2)
        self.alert.assert_called_once()

    def test_new_identity_has_new_id_and_terminal_uses_same_id_as_main(self):
        path = self.payload("waiting/old")
        with mock.patch.object(watch, "enqueue_main", return_value=True) as main, \
                mock.patch.object(watch, "wake_main"):
            watch.tick(self.env, 1000)
            first = main.call_args.args[1]
            self.assertEqual(self.alert.call_args.args[0]["PANE_TELL_ALERT_EVENT_ID"], first)
            path.write_text("different synthetic message")
            os.utime(path, (300, 300))
            watch.tick(self.env, 1200)
            self.assertNotEqual(first, main.call_args.args[1])

    def test_periodic_recovery_runs_before_stall_alert_threshold(self):
        self.payload("batch/term_test/open/123.1", age=121)
        with mock.patch.object(watch, "recover_batches") as recover, \
                mock.patch.object(watch, "wake_main"):
            watch.tick(self.env, 1000)
        recover.assert_called_once_with(self.env)
        self.alert.assert_not_called()

    def test_gone_source_keeps_retrying_pending_main_inbox(self):
        self.root.mkdir()
        with mock.patch.object(watch, "wake_main") as wake:
            watch.tick(self.env, 1000)
            watch.tick(self.env, 1100)
        self.assertEqual(wake.call_count, 2)

    def test_failed_main_import_retries_after_original_disappears(self):
        source = self.payload("waiting/old")
        inbox = load("relay_inbox_locked", "main-alert-inbox.py")
        with mock.patch.object(watch, "enqueue_main", return_value=False), \
                mock.patch.object(watch, "wake_main"):
            watch.tick(self.env, 1000)
        source.unlink()
        with mock.patch.object(watch, "wake_main"):
            watch.tick(self.env, 1100)
        events = inbox.pending(self.env)
        self.assertEqual(len(events), 1)
        self.assertNotIn("PRIVATE", events[0]["body"])
        self.assertEqual(self.alert.call_count, 1)

    def test_failed_terminal_import_retries_after_original_disappears(self):
        source = self.payload("waiting/old")
        self.alert.side_effect = [False, True]
        with mock.patch.object(watch, "wake_main"):
            watch.tick(self.env, 1000)
            source.unlink()
            watch.tick(self.env, 1100)
            watch.tick(self.env, 1200)
        self.assertEqual(self.alert.call_count, 2)

    def test_unsafe_outbox_is_preserved_without_transport(self):
        self.payload("waiting/old")
        target = self.root / ".watch-pending.json"
        os.mkfifo(target)
        with mock.patch.object(watch, "wake_main"):
            self.assertEqual(watch.tick(self.env, 1000), [])
        self.assertTrue(target.is_fifo())
        self.alert.assert_not_called()

    def test_special_watch_metadata_does_not_block(self):
        self.payload("waiting/old")
        lock = self.root / ".watch.lock"
        os.mkfifo(lock)
        self.assertEqual(watch.scan(self.env, 1000), [])
        lock.unlink()
        os.mkfifo(self.root / ".watch-alerts.json")
        watch.scan(self.env, 1000)
        self.alert.assert_called_once()

    def test_real_inbox_survives_main_absence_restart_and_explicit_receipt(self):
        source = self.payload("waiting/old")
        inbox = load("relay_inbox_integration", "main-alert-inbox.py")
        self.alert.return_value = False
        with mock.patch.object(watch, "wake_main"):
            watch.tick(self.env, 1000)
        event = inbox.pending(self.env)[0]
        self.assertIsNone(inbox.drain(self.env, "term_MAIN", "session1", "UserPromptSubmit", now=1000))
        root = Path(self.tmp.name) / "orchestrator"
        (root / "main").write_text("term_MAIN\n")
        first = inbox.drain(self.env, "term_MAIN", "session1", "UserPromptSubmit", now=1001)
        self.assertIn(event["event_id"], first["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(len(inbox.pending(self.env)), 1)
        inbox.drain(self.env, "term_MAIN", "session2", "SessionStart", now=1002)
        state = json.loads((root / "alerts/state.json").read_text())
        claim = state["claim"]
        self.assertEqual(claim["session_id"], "session2")
        inbox.ack(self.env, "term_MAIN", "session2", claim["token"], claim["event_ids"], now=1003)
        with mock.patch.object(watch, "wake_main"):
            watch.tick(self.env, 1004)
        self.assertEqual(inbox.pending(self.env), [])
        self.assertTrue(source.exists())  # Receipt does not deliver or delete the original.


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.argv.remove("--selftest")
    unittest.main()
