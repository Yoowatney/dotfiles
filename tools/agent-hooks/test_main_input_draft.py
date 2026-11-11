import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("draft", HERE / "main-input-draft.py")
D = importlib.util.module_from_spec(spec)
spec.loader.exec_module(D)


class DraftTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / "state"
        (self.state / "orchestrator").mkdir(parents=True)
        (self.state / "orchestrator/main").write_text("test-terminal\n")
        self.pane = {"pane_id": "wT:p1", "terminal_id": "test-terminal", "agent": "claude",
                     "agent_status": "idle", "scroll": {"offset_from_bottom": 0}}
        self.screen = "─" * 40 + "\n❯\xa0first 한글\n  second\n" + "─" * 40 + "\n"
        self.keys = []
        self.env = mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.state), "PANE_TELL_SETTLE": "0"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.run = mock.patch.object(D, "herdr", side_effect=self.herdr)
        self.run.start()
        self.addCleanup(self.run.stop)

    def herdr(self, *args):
        if args[:2] == ("pane", "get"):
            return json.dumps({"result": {"pane": self.pane}})
        if args[:2] == ("pane", "read"):
            return self.screen
        if args[:2] == ("agent", "send-keys"):
            self.keys.extend(args[3:])
            self.screen = "─" * 40 + "\n❯\xa0\n" + "─" * 40 + "\n"
            return ""
        raise AssertionError(args)

    def test_main_draft_is_saved_before_clear_and_notice_survives_retry(self):
        self.assertTrue(D.prepare("wT:p1", "claude", check=True))
        self.assertEqual(self.keys, [])
        notice = D.prepare("wT:p1", "claude")
        path = Path(notice.split(": ", 1)[1])
        saved = json.loads(path.read_text())
        self.assertEqual(saved["rendered_text"], "first 한글\nsecond")
        self.assertIn("❯", saved["ansi_box"])
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertTrue(self.keys)
        self.assertNotIn("Enter", self.keys)
        self.assertEqual(D.prepare("wT:p1", "claude"), notice)
        self.pane["agent_status"] = "blocked"
        self.assertEqual(D.notice("wT:p1"), notice)
        D.ack("wT:p1")
        self.pane["agent_status"] = "idle"
        self.assertEqual(D.prepare("wT:p1", "claude"), "")

    def test_non_main_and_blocked_or_scrolled_never_clear(self):
        for field, value in [("terminal_id", "other"), ("agent_status", "blocked"),
                             ("agent_status", "unknown"), ("agent_status", None),
                             ("scroll", {}), ("scroll", {"offset_from_bottom": 1})]:
            original = self.pane[field]
            self.pane[field] = value
            with self.assertRaises(D.Unsafe):
                D.prepare("wT:p1", "claude")
            self.pane[field] = original
        self.assertEqual(self.keys, [])

    def test_unreadable_or_collapsed_paste_never_clears(self):
        for screen in ["confirmation dialog", self.screen.replace("first 한글", "[Pasted text #1 +20 lines]")]:
            self.screen = screen
            with self.assertRaises(D.Unsafe):
                D.prepare("wT:p1", "claude")
        self.assertEqual(self.keys, [])

    def test_save_failure_never_clears(self):
        with mock.patch.object(D, "save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                D.prepare("wT:p1", "claude")
        self.assertEqual(self.keys, [])

    def test_codex_continuation_prompt_mark_keeps_the_whole_draft(self):
        self.screen = "\x1b[48;2;30;30;30m› first\n\x1b[48;2;30;30;30m  › second\n\x1b[48;2;30;30;30m  \n"
        self.assertEqual(D.draft("wT:p1", "codex")["rendered_text"], "first\n› second")

    def test_changing_draft_never_clears(self):
        with mock.patch.object(D, "draft", side_effect=[{"rendered_text": "first"}, {"rendered_text": "changed"}]):
            with self.assertRaises(D.Unsafe):
                D.prepare("wT:p1", "claude")
        self.assertEqual(self.keys, [])


if __name__ == "__main__":
    import unittest.mock
    unittest.main()
