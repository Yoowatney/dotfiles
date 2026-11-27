#!/usr/bin/env python3
"""Offline durability and ownership boundaries for refresh recovery."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("recovery", HERE / "main-refresh-recovery.py")
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


class Recovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.gate = self.root / "gate.json"
        self.memo = self.root / "w1:p1.md"
        self.memo.write_text("original handoff")
        self.gate.write_text(json.dumps(dict(pid=os.getpid(), terminal="main", session="old")))
        recovery.prepare(self.gate, self.memo, "w1:p1", time.time() - 1)
        self.data = json.loads(self.gate.read_text())
        self.folder = Path(self.data["recovery"])

    def tearDown(self):
        self.tmp.cleanup()

    def test_expired_prepare_holds_until_clear_accepted(self):
        self.assertTrue(recovery.blocked(self.gate, dict(agent_session=dict(value="old"))))
        recovery.accept(self.gate)
        self.assertFalse(recovery.blocked(self.gate, dict(agent_session=dict(value="old"))))

    def test_priority_skips_only_the_preparation_gate(self):
        pane = dict(agent_session=dict(value="old"))
        self.gate.write_text(json.dumps(dict(pid=os.getpid(), terminal="main", session="old")))
        self.assertFalse(recovery.blocked(self.gate, pane, "1"))
        recovery.prepare(self.gate, self.memo, "w1:p1", time.time() + 30)
        self.assertTrue(recovery.blocked(self.gate, pane, "1"))

    def test_hold_keeps_relays_back_at_most_ten_minutes_before_the_clear(self):
        # 11/25 and 11/27: one hold after another kept every relay from the main pane for 1 h 45 min and for 8 h.
        self.gate.write_text(json.dumps(dict(pid=os.getpid(), terminal="main", session="old")))
        pane = lambda status: dict(agent_session=dict(value="old"), agent_status=status)
        self.assertTrue(recovery.blocked(self.gate, pane("working")))
        old = time.time() - 601
        os.utime(self.gate, (old, old))
        for status in ("working", "idle", "done"):
            with self.subTest(status=status):
                self.assertFalse(recovery.blocked(self.gate, pane(status)))
        recovery.prepare(self.gate, self.memo, "w1:p1", time.time() + 30)
        os.utime(self.gate, (old, old))
        self.assertTrue(recovery.blocked(self.gate, pane("working")))  # never during the /clear itself

    def test_old_sender_two_argument_blocked_call_still_holds(self):
        row = json.dumps(dict(result=dict(pane=dict(agent_session=dict(value="old")))))
        with patch.object(recovery.sys, "argv", ["main-refresh-recovery.py", "blocked", str(self.gate), row]):
            self.assertEqual(recovery.main(), 0)

    def test_old_sender_five_argument_record_call_still_delivers(self):
        with patch.object(recovery.sys, "argv", ["main-refresh-recovery.py", "record",
                                                 str(self.root), "main", "old", "queued relay", "1"]):
            self.assertEqual(recovery.main(), 0)
        self.assertIn("queued relay", (self.folder / "relays.jsonl").read_text())

    def test_replaced_gate_does_not_hide_recovery(self):
        self.gate.write_text('{}')
        self.memo.unlink()
        note = recovery.notice(self.memo)
        self.assertIn(str(self.folder / "handoff.md"), note)
        recovery.record(self.root, "main", "old", "after replacement")
        self.assertIn("after replacement", (self.folder / "relays.jsonl").read_text())

    def test_unrelated_pane_load_survives_main_journal_failure(self):
        journal = self.folder / "relays.jsonl"
        journal.unlink()
        journal.mkdir()
        self.assertEqual(recovery.notice(self.root / "w2:p2.md"), "")

    def test_confirmed_transition_does_not_activate_later(self):
        recovery.complete(self.gate)
        self.assertEqual(recovery.notice(self.memo), "")
        recovery.record(self.root, "main", "new", "normal relay")
        self.assertEqual((self.folder / "relays.jsonl").read_text(), "")

    def test_resolve_ends_an_active_record_and_keeps_its_files(self):
        recovery.activate(self.data)
        self.assertIn(str(self.folder / "handoff.md"), recovery.notice(self.memo))
        self.assertIn("resolve", recovery.notice(self.memo))
        recovery.resolve(self.folder)
        self.assertEqual(recovery.notice(self.memo), "")
        self.assertFalse((self.folder / "active").exists())
        self.assertTrue((self.folder / "completed").exists())
        for name in ("handoff.md", "relays.jsonl", "metadata.json"):
            self.assertTrue((self.folder / name).exists())

    def test_aborted_prepare_does_not_activate_later(self):
        recovery.complete(self.gate)
        self.assertTrue((self.folder / "completed").exists())
        self.assertEqual(recovery.notice(self.memo), "")

    def test_late_transition_keeps_original_transcript_and_all_relays(self):
        transcript = self.root / "original-session.jsonl"
        transcript.touch()
        Path(str(self.memo) + ".transcript").write_text(str(transcript))
        recovery.prepare(self.gate, self.memo, "w1:p1", time.time() - 1)
        folder = Path(json.loads(self.gate.read_text())["recovery"])
        Path(str(self.memo) + ".transcript").write_text("new-session.jsonl")
        recovery.record(self.root, "main", "old", "before transition")
        recovery.record(self.root, "main", "new", "after transition")
        self.assertIn(str(transcript), recovery.notice(self.memo))
        items = [json.loads(line) for line in (folder / "relays.jsonl").read_text().splitlines()]
        self.assertEqual([(item["session"], item["message"]) for item in items],
                         [("old", "before transition"), ("new", "after transition")])

    def test_incomplete_prepare_does_not_obstruct_unrelated_relay(self):
        root = self.root / "other"
        root.mkdir()
        gate, memo = root / "gate.json", root / "w9:p9.md"
        gate.write_text(json.dumps(dict(pid=os.getpid(), terminal="other", session="old")))
        memo.write_text("another handoff")
        original_write = recovery.write
        def failing_write(path, text):
            if path.name == "relays.jsonl":
                raise OSError("disk failed during snapshot preparation")
            return original_write(path, text)
        with patch.object(recovery, "write", side_effect=failing_write):
            with self.assertRaises(OSError):
                recovery.prepare(gate, memo, "w9:p9", time.time() - 1)
        recovery.record(root, "other", "old", "ordinary relay")
        self.assertEqual(json.loads(gate.read_text()).get("clearing"), None)

    def test_fsync_failure_prevents_record_success(self):
        recovery.activate(self.data)
        with patch.object(recovery.os, "fsync", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                recovery.record(self.root, "main", "old", "must remain queued")


if __name__ == "__main__":
    unittest.main()
