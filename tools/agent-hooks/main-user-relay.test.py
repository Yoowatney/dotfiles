#!/usr/bin/env python3
"""Synthetic relay fixtures only; no herdr or Slack calls."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
PATH = HERE / "main-user-relay.py"
spec = importlib.util.spec_from_file_location("main_user_relay", PATH)
relay = importlib.util.module_from_spec(spec) if PATH.exists() else None
if relay:
    spec.loader.exec_module(relay)

PREFIX = "[pane-tell: 'Synthetic sender' 세션이 보낸 글입니다. 요유가 이 pane에 직접 친 글이 아니므로, 되돌리기 어려운 일은 요유에게 확인하세요] "
TEXT = "[Slack #synthetic 요유] Synthetic request\nKeep the entire second line. (ts 123.000001, 글 CSYNTHETIC/123.000001)"


class RelayTest(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(relay, "main user relay helper is not implemented")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = {"HOME": self.tmp.name, "XDG_STATE_HOME": self.tmp.name, "HERDR_SOCKET_PATH": ""}
        (self.root / "orchestrator").mkdir()
        (self.root / "orchestrator/main").write_text("term_main\n")
        for name in ("waiting", "receivers"):
            (self.root / "pane-tell" / name).mkdir(parents=True)

    def waiting(self, name="123.fixture", body=PREFIX + TEXT, terminal="term_main", socket="default"):
        path = self.root / "pane-tell/waiting" / name
        path.write_text(body)
        (self.root / "pane-tell/receivers" / name).write_text(terminal + "\n" + socket + "\n")
        return path

    def identity(self, path):
        info = path.stat()
        return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]

    def drain(self, now=100, session="session_one", terminal="term_main"):
        return relay.drain(self.env, terminal, session, "UserPromptSubmit", now=now)

    def test_original_is_complete_and_source_is_not_modified(self):
        body = PREFIX + TEXT + "\n" + ("long original " * 1000) + "FINAL_MARKER"
        path = self.waiting(body=body)
        before = path.read_bytes()
        context = self.drain()
        self.assertIn(body, context)
        self.assertIn("FINAL_MARKER", context)
        self.assertIn("not a Slack API authorization", context)
        self.assertEqual(path.read_bytes(), before)
        self.assertTrue((self.root / "pane-tell/receivers" / path.name).exists())

    def test_inline_or_quoted_headers_are_not_originals(self):
        for body in (TEXT, PREFIX + "A report quoting " + TEXT,
                     PREFIX + "A report\n\n" + TEXT, PREFIX + "> " + TEXT,
                     PREFIX + "A report quoting a complete envelope\n\n" + PREFIX + TEXT):
            self.waiting(body=body)
            self.assertEqual(relay.collect(self.env, "term_main"), [], body)

    def test_reactions_and_explanation_requests_and_multiple_batch_originals(self):
        reaction = "[Slack #synthetic 요유 반응] 'Synthetic'에 :eyes: (ts 123.000002)"
        explanation = "[Slack #synthetic 설명 요청] 'Synthetic'에 :question: (ts 123.000003, 항목 확인 필요)"
        body = PREFIX + TEXT + "\n\n" + PREFIX + reaction + "\n\n" + PREFIX + explanation
        self.waiting(body=body)
        context = self.drain()
        for original in (TEXT, reaction, explanation):
            self.assertIn(original, context)
        self.assertEqual(context.count(TEXT), 1)

    def test_deterministic_batch_finds_later_user_original_after_non_user_report(self):
        self.waiting(name="batch-syntheticgeneration", body=PREFIX + "Synthetic worker report\n\n" + PREFIX + TEXT)
        context = self.drain()
        self.assertIn(TEXT, context)
        self.assertNotIn("Synthetic worker report", context)

    def test_deleted_or_replaced_original_is_never_reoffered(self):
        path = self.waiting()
        record = relay.collect(self.env, "term_main")[0]
        self.assertTrue(relay.active(self.env, record["source"]))
        self.drain()
        path.unlink()
        self.assertFalse(relay.active(self.env, record["source"]))
        self.assertIsNone(self.drain(now=200))
        path.write_text(PREFIX + TEXT)
        self.assertFalse(relay.active(self.env, record["source"]))
        self.assertIsNotNone(self.drain(now=201))

    def test_offer_is_lease_not_read_receipt_and_restart_can_offer(self):
        self.waiting()
        self.assertIsNotNone(self.drain())
        self.assertIsNone(self.drain(now=101))
        self.assertIsNotNone(self.drain(now=161))
        self.assertIsNotNone(self.drain(now=162, session="session_two"))
        journal = self.root / "orchestrator/user-relays/state.json"
        saved = journal.read_text()
        self.assertNotIn("Synthetic request", saved)
        self.assertNotIn("consumed", saved)
        self.assertEqual(stat.S_IMODE(journal.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(journal.parent.stat().st_mode), 0o700)

    def test_main_and_receiver_routes_are_mandatory_but_classifier_survives_main_absence(self):
        path = self.waiting()
        identity = self.identity(path)
        (self.root / "orchestrator/main").unlink()
        self.assertTrue(relay.is_user_relay(self.env, str(path), identity))
        self.assertIsNone(self.drain())
        (self.root / "orchestrator/main").write_text("term_main\n")
        self.assertIsNone(self.drain(terminal="term_other"))
        for socket in ("/fake/sessions/workers/herdr.sock", "/fake/sessions/other/herdr.sock", "garbage"):
            self.waiting(socket=socket)
            self.assertFalse(relay.is_user_relay(self.env, str(path), identity))
            self.assertIsNone(self.drain())
        self.waiting(terminal="term_other")
        self.assertIsNone(self.drain())

    def test_explicit_default_socket_is_accepted(self):
        self.waiting(socket="/synthetic/sessions/default/herdr.sock")
        self.assertIsNotNone(self.drain())

    def test_named_server_hook_cannot_claim_default_main_context(self):
        path = self.waiting()
        self.env["HERDR_SOCKET_PATH"] = "/synthetic/sessions/workers/herdr.sock"
        self.assertTrue(relay.is_user_relay(self.env, str(path), self.identity(path)))
        self.assertEqual(relay.collect(self.env, "term_main"), [])
        self.assertIsNone(self.drain())

    def test_journal_parent_symlink_is_rejected_without_target_writes(self):
        self.waiting()
        target = self.root / "unrelated"
        target.mkdir()
        (self.root / "orchestrator/user-relays").symlink_to(target)
        with self.assertRaises((OSError, ValueError)):
            self.drain()
        self.assertEqual(list(target.iterdir()), [])

    def test_symlink_fifo_and_parent_symlink_are_rejected_without_blocking(self):
        path = self.waiting()
        outside = self.root / "outside"
        outside.write_text(PREFIX + TEXT)
        path.unlink()
        path.symlink_to(outside)
        self.assertIsNone(self.drain())
        path.unlink()
        os.mkfifo(path)
        self.assertIsNone(self.drain())
        path.unlink()
        waiting = path.parent
        waiting.rmdir()
        waiting.symlink_to(self.root)
        self.assertEqual(relay.collect(self.env, "term_main"), [])

    def test_unsafe_receiver_and_main_are_rejected(self):
        path = self.waiting()
        receiver = self.root / "pane-tell/receivers" / path.name
        receiver.unlink()
        os.mkfifo(receiver)
        self.assertIsNone(self.drain())
        receiver.unlink()
        receiver.write_text("term_main\ndefault\n")
        main = self.root / "orchestrator/main"
        main.unlink()
        os.mkfifo(main)
        self.assertIsNone(self.drain())

    def test_active_rechecks_receiver_and_registration_identity(self):
        path = self.waiting()
        record = relay.collect(self.env, "term_main")[0]
        receiver = self.root / "pane-tell/receivers" / path.name
        receiver.write_text("term_other\ndefault\n")
        self.assertFalse(relay.active(self.env, record["source"]))
        self.waiting()
        record = relay.collect(self.env, "term_main")[0]
        (self.root / "orchestrator/main").write_text("term_other\n")
        self.assertFalse(relay.active(self.env, record["source"]))

    def test_source_disappears_between_collection_and_output(self):
        path = self.waiting()
        original = relay.active
        def vanished(env, source):
            path.unlink(missing_ok=True)
            return original(env, source)
        with patch.object(relay, "active", side_effect=vanished):
            self.assertIsNone(self.drain())

    def test_source_delivered_during_offer_journal_write_is_not_returned(self):
        path = self.waiting()
        save = relay._save
        def delivered(directory, data):
            save(directory, data)
            path.unlink()
        with patch.object(relay, "_save", side_effect=delivered):
            self.assertIsNone(self.drain())

    def test_replacement_during_read_is_rejected(self):
        path = self.waiting()
        real_stat = os.stat
        directory_inode = path.parent.stat().st_ino
        changed = False
        def replaced(name, *args, **kwargs):
            nonlocal changed
            if (name == path.name and kwargs.get("dir_fd") is not None and
                    os.fstat(kwargs["dir_fd"]).st_ino == directory_inode and not changed):
                changed = True
                replacement = path.with_name("replacement")
                replacement.write_text(PREFIX + TEXT)
                replacement.replace(path)
            return real_stat(name, *args, **kwargs)
        with patch.object(relay.os, "stat", side_effect=replaced):
            self.assertEqual(relay.collect(self.env, "term_main"), [])
        self.assertTrue(changed)

    def test_non_owner_regular_file_is_rejected(self):
        path = self.waiting()
        descriptor = os.open(path, os.O_RDONLY)
        self.addCleanup(os.close, descriptor)
        identity = os.fstat(descriptor)
        real_fstat = os.fstat
        def unowned(fd):
            result = real_fstat(fd)
            if result.st_ino == identity.st_ino:
                values = list(result)
                values[4] = os.getuid() + 1
                return os.stat_result(values)
            return result
        with patch.object(relay.os, "fstat", side_effect=unowned):
            self.assertIsNone(self.drain())

    def test_journal_lock_contention_preserves_source_and_never_claims(self):
        import fcntl
        self.waiting()
        self.drain()
        lock = self.root / "orchestrator/user-relays/lock"
        with lock.open("r+") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                self.drain(now=200)
        self.assertIsNotNone(self.drain(now=200))

    def test_aggregate_limit_defers_whole_original_without_truncation(self):
        self.waiting(name="123.first", body=PREFIX + TEXT + "FIRST_END")
        self.waiting(name="124.second", body=PREFIX + TEXT + "SECOND_END")
        with patch.object(relay, "MAX_CONTEXT_BYTES", 450):
            context = self.drain()
        self.assertIn("FIRST_END", context)
        self.assertNotIn("SECOND_END", context)
        self.assertIn("not truncated", context)
        context = self.drain(now=101)
        self.assertIn("SECOND_END", context)

    def test_oversize_is_explicit_and_never_returns_a_partial_original(self):
        self.waiting(body=PREFIX + TEXT + "X" * 10000)
        with patch.object(relay, "MAX_PAYLOAD_BYTES", 1024):
            context = self.drain()
        self.assertIn("exceeds", context)
        self.assertNotIn("Synthetic request", context)

    def test_journal_symlink_fifo_or_corruption_never_overwrites_target(self):
        self.waiting()
        self.drain()
        journal = self.root / "orchestrator/user-relays/state.json"
        journal.write_text("{broken")
        with self.assertRaises((ValueError, OSError)):
            self.drain(now=200)
        self.assertEqual(journal.read_text(), "{broken")
        journal.unlink()
        os.mkfifo(journal)
        with self.assertRaises((ValueError, OSError)):
            self.drain(now=200)
        journal.unlink()
        target = self.root / "target"
        target.write_text("keep")
        journal.symlink_to(target)
        with self.assertRaises((ValueError, OSError)):
            self.drain(now=200)
        self.assertEqual(target.read_text(), "keep")

    def test_cli_emits_plain_complete_context_and_nothing_for_wrong_terminal(self):
        self.waiting()
        command = [sys.executable, "-I", str(PATH), "drain", "--terminal", "term_other",
                   "--session", "session_one", "--hook-event", "Stop"]
        result = subprocess.run(command, env={**os.environ, **self.env}, capture_output=True, text=True, timeout=3)
        self.assertEqual((result.returncode, result.stdout), (0, ""))
        command[command.index("term_other")] = "term_main"
        result = subprocess.run(command, env={**os.environ, **self.env}, capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(PREFIX + TEXT, result.stdout)


if __name__ == "__main__":
    unittest.main()
