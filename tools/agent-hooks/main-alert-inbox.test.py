#!/usr/bin/env python3
"""Synthetic local inbox tests; never contact a user session or external service."""
import importlib.util
import fcntl
import json
import os
from pathlib import Path
import subprocess
import stat
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
PATH = HERE / "main-alert-inbox.py"
spec = importlib.util.spec_from_file_location("main_alert_inbox", PATH)
inbox = importlib.util.module_from_spec(spec) if PATH.exists() else None
if inbox:
    spec.loader.exec_module(inbox)

ONE = "86fa69e7-62d1-4ccf-b884-b049a8fa1f37"
TWO = "75f172d1-ff8a-447e-9a45-35fb664ac9d0"


class InboxTest(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(inbox, "durable main inbox helper is not implemented")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = {"HOME": self.tmp.name, "XDG_STATE_HOME": self.tmp.name}
        self.registration = Path(self.tmp.name) / "orchestrator/main"
        self.registration.parent.mkdir()
        self.registration.write_text("term_main\n")

    def enqueue(self, event_id=ONE):
        return inbox.enqueue(self.env, event_id, "Synthetic delay", "Private archive: /synthetic/queue")

    def offer(self, **kwargs):
        return inbox.drain(self.env, "term_main", "session_one", "UserPromptSubmit", **kwargs)

    def claim(self):
        return json.loads((Path(self.tmp.name) / "orchestrator/alerts/state.json").read_text())["claim"]

    def test_enqueue_survives_reload_and_idempotent_retry(self):
        self.assertTrue(self.enqueue())
        self.assertFalse(self.enqueue())
        spec.loader.exec_module(inbox)
        context = self.offer(now=100)
        self.assertIn(ONE, context["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(context["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")

    def test_conflicting_id_is_rejected_without_replacing_original(self):
        self.enqueue()
        with self.assertRaises(ValueError):
            inbox.enqueue(self.env, ONE, "Changed", "Changed")
        self.assertIn("Synthetic delay", self.offer(now=100)["hookSpecificOutput"]["additionalContext"])

    def test_main_absence_and_other_terminal_never_claim(self):
        self.enqueue()
        self.registration.unlink()
        self.assertIsNone(self.offer(now=100))
        self.registration.write_text("term_other\n")
        self.assertIsNone(self.offer(now=101))
        self.registration.write_text("term_main\n")
        self.assertIsNotNone(self.offer(now=102))

    def test_output_is_not_consumption_and_same_claim_retries(self):
        self.enqueue()
        self.offer(now=100)
        token = self.claim()["token"]
        self.assertIsNone(self.offer(now=101))
        self.assertIsNotNone(self.offer(now=161))
        self.assertEqual(token, self.claim()["token"])
        self.assertTrue(self.enqueue(TWO))

    def test_explicit_ack_only_consumes_claimed_events_and_is_idempotent(self):
        self.enqueue()
        self.offer(now=100)
        token = self.claim()["token"]
        self.enqueue(TWO)
        with self.assertRaises(ValueError):
            inbox.ack(self.env, "term_main", "session_one", token, [TWO], now=101)
        receipt = inbox.ack(self.env, "term_main", "session_one", token, [ONE], now=102)
        self.assertEqual(receipt["event_ids"], [ONE])
        self.assertEqual(receipt, inbox.ack(self.env, "term_main", "session_one", token, [ONE], now=103))
        context = self.offer(now=104)["hookSpecificOutput"]["additionalContext"]
        self.assertIn(TWO, context)
        self.assertNotIn(ONE, context)

    def test_restart_reclaims_pending_and_rejects_old_receipt(self):
        self.enqueue()
        self.offer(now=100)
        old = self.claim()["token"]
        context = inbox.drain(self.env, "term_main", "session_two", "SessionStart", now=101)
        self.assertIn(ONE, context["hookSpecificOutput"]["additionalContext"])
        with self.assertRaises(ValueError):
            inbox.ack(self.env, "term_main", "session_one", old, [ONE], now=102)
        current = self.claim()["token"]
        inbox.ack(self.env, "term_main", "session_two", current, [ONE], now=103)
        self.assertIsNone(inbox.drain(self.env, "term_main", "session_two", "Stop", now=104))

    def test_private_permissions_and_corruption_refuses_overwrite(self):
        self.enqueue()
        root = Path(self.tmp.name) / "orchestrator/alerts"
        self.assertEqual(root.stat().st_mode & 0o777, 0o700)
        self.assertEqual((root / "state.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual((root / "lock").stat().st_mode & 0o777, 0o600)
        (root / "state.json").write_text("{broken")
        with self.assertRaises(ValueError):
            self.enqueue(TWO)
        self.assertEqual((root / "state.json").read_text(), "{broken")

    def test_registration_change_rejects_ack_and_preserves_event(self):
        self.enqueue()
        self.offer(now=100)
        token = self.claim()["token"]
        self.registration.write_text("term_new\n")
        with self.assertRaises(ValueError):
            inbox.ack(self.env, "term_main", "session_one", token, [ONE], now=101)
        self.assertIsNotNone(inbox.drain(self.env, "term_new", "session_two", "SessionStart", now=102))

    def test_symlink_state_is_rejected_without_touching_target(self):
        self.enqueue()
        root = Path(self.tmp.name) / "orchestrator/alerts"
        target = Path(self.tmp.name) / "unrelated"
        target.write_text("keep")
        (root / "state.json").unlink()
        (root / "state.json").symlink_to(target)
        with self.assertRaises((OSError, ValueError)):
            self.enqueue(TWO)
        self.assertEqual(target.read_text(), "keep")

    def test_pending_is_read_only_and_sha_identifier_and_timestamp_survive(self):
        event_id = "a" * 64
        inbox.enqueue(self.env, event_id, "Synthetic", "Metadata only", now=42)
        path = Path(self.tmp.name) / "orchestrator/alerts/state.json"
        before = path.read_bytes()
        self.assertEqual(inbox.pending(self.env), [{"event_id": event_id, "title": "Synthetic",
                                                  "body": "Metadata only", "created_at": 42}])
        self.assertEqual(before, path.read_bytes())
        self.offer(now=100)
        inbox.ack(self.env, "term_main", "session_one", self.claim()["token"], [event_id], now=101)
        self.assertEqual(inbox.pending(self.env), [])
        self.assertFalse(inbox.enqueue(self.env, event_id, "Synthetic", "Metadata only", now=102))

    def test_korean_maximum_body_fits_and_multiple_events_are_bounded(self):
        inbox.enqueue(self.env, ONE, "경보", "가" * 1000)
        inbox.enqueue(self.env, TWO, "두 번째 경보", "나" * 1000)
        context = self.offer(now=100)["hookSpecificOutput"]["additionalContext"]
        self.assertLessEqual(len(context.encode()), 6000)
        self.assertEqual(len(self.claim()["event_ids"]), 1)
        self.assertIn("Unlisted pending alerts: 1", context)

    def test_backward_clock_reoffers_without_losing_claim(self):
        self.enqueue()
        self.offer(now=100)
        token = self.claim()["token"]
        self.assertIsNotNone(self.offer(now=50))
        self.assertEqual(token, self.claim()["token"])

    def test_lock_contention_preserves_state_and_retries(self):
        self.enqueue()
        root = Path(self.tmp.name) / "orchestrator/alerts"
        before = (root / "state.json").read_bytes()
        with (root / "lock").open("r+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                self.enqueue(TWO)
            self.assertEqual(before, (root / "state.json").read_bytes())
        self.assertTrue(self.enqueue(TWO))

    def test_corrupt_receipt_association_is_rejected(self):
        self.enqueue()
        self.offer(now=100)
        inbox.ack(self.env, "term_main", "session_one", self.claim()["token"], [ONE], now=101)
        path = Path(self.tmp.name) / "orchestrator/alerts/state.json"
        data = json.loads(path.read_text())
        next(iter(data["receipts"].values()))["event_ids"] = [TWO]
        path.write_text(json.dumps(data))
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            self.enqueue(TWO)
        self.assertEqual(before, path.read_bytes())

    def test_cli_drain_and_explicit_ack_are_separate(self):
        self.enqueue()
        base = [sys.executable, "-I", str(PATH)]
        env = dict(os.environ, **self.env)
        result = subprocess.run(base + ["drain", "--terminal", "term_main", "--session", "session_cli",
                                       "--hook-event", "SessionStart"], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(ONE, json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(len(inbox.pending(self.env)), 1)
        args = ["ack", "--terminal", "term_main", "--session", "session_cli", "--token",
                self.claim()["token"], "--ids", ONE]
        result = subprocess.run(base + args, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["event_ids"], [ONE])
        self.assertEqual(inbox.pending(self.env), [])

    def test_escape_heavy_record_is_refused_before_it_can_block_the_queue(self):
        with self.assertRaises(ValueError):
            inbox.enqueue(self.env, ONE, "Synthetic", "\\" * 3000)
        self.assertEqual(inbox.pending(self.env), [])
        self.enqueue(TWO)
        self.assertIn(TWO, self.offer()["hookSpecificOutput"]["additionalContext"])

    def test_failed_atomic_replace_keeps_previous_state_and_no_temporary_file(self):
        from unittest.mock import patch
        self.enqueue()
        root = Path(self.tmp.name) / "orchestrator/alerts"
        before = (root / "state.json").read_bytes()
        with patch.object(inbox.os, "replace", side_effect=OSError("synthetic disk failure")):
            with self.assertRaises(OSError):
                self.enqueue(TWO)
        self.assertEqual(before, (root / "state.json").read_bytes())
        self.assertEqual(list(root.glob(".state-*")), [])

    def test_fifo_state_fails_without_waiting_for_a_writer_or_replacing_it(self):
        self.enqueue()
        state = Path(self.tmp.name) / "orchestrator/alerts/state.json"
        state.unlink()
        os.mkfifo(state)
        result = subprocess.run([sys.executable, "-I", str(PATH), "drain", "--terminal", "term_main",
                                 "--session", "session_one", "--hook-event", "UserPromptSubmit"],
                                env=dict(os.environ, **self.env), capture_output=True, text=True, timeout=1)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertTrue(stat.S_ISFIFO(state.stat().st_mode))

    def test_fifo_main_registration_is_unknown_without_waiting_or_claiming(self):
        self.enqueue()
        state = Path(self.tmp.name) / "orchestrator/alerts/state.json"
        before = state.read_bytes()
        self.registration.unlink()
        os.mkfifo(self.registration)
        result = subprocess.run([sys.executable, "-I", str(PATH), "drain", "--terminal", "term_main",
                                 "--session", "session_one", "--hook-event", "SessionStart"],
                                env=dict(os.environ, **self.env), capture_output=True, text=True, timeout=1)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(state.read_bytes(), before)
        self.assertTrue(stat.S_ISFIFO(self.registration.stat().st_mode))


if __name__ == "__main__":
    unittest.main(argv=[__file__], verbosity=2)
