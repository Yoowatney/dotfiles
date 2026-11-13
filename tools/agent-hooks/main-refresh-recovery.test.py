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
