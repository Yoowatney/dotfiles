"""Public, synthetic quota fixtures: never read the operator's real account."""
import datetime
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("agent-quota")


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.now = 1800000000
        self.env = patch.dict(os.environ, {"HOME": str(self.home),
            "CODEX_HOME": str(self.home / ".codex"),
            "CLAUDE_CONFIG_DIR": str(self.home / ".claude"),
            "XDG_STATE_HOME": str(self.home / "state")}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        loader = importlib.machinery.SourceFileLoader("agent_quota", str(SCRIPT))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        self.q = importlib.util.module_from_spec(spec)
        loader.exec_module(self.q)
        self.write(".codex/auth.json", {"auth_mode": "chatgpt", "tokens": {"account_id": "test-codex"}})
        os.utime(self.home / ".codex/auth.json", (self.now - 1000,) * 2)
        self.write(".claude.json", {"oauthAccount": {"accountUuid": "test-claude", "hasExtraUsageEnabled": False}})
        self.event()
        self.capture()

    def write(self, path, value):
        path = self.home / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        return path

    def event(self, used=40, short=20, age=0, credits=False, file="a.jsonl", reset=None):
        at = self.now - age
        row = {"timestamp": datetime.datetime.fromtimestamp(at, datetime.timezone.utc).isoformat(),
            "type": "event_msg", "payload": {"type": "token_count", "rate_limits": {
                "limit_id": "codex", "primary": {"used_percent": short, "window_minutes": 300, "resets_at": self.now + 1000},
                "secondary": {"used_percent": used, "window_minutes": 10080, "resets_at": reset or self.now + 302400},
                "credits": {"has_credits": credits, "unlimited": False}}}}
        path = self.write(".codex/sessions/" + file, row)
        os.utime(path, (self.now,) * 2)
        return path

    def capture(self, used=40, short=20, tick=100, now=None):
        stamp = self.now if now is None else now
        transcript = self.home / "transcript.jsonl"
        if not transcript.exists() or tick != 100:
            self.write("transcript.jsonl", {"type": "assistant", "timestamp": datetime.datetime.fromtimestamp(stamp, datetime.timezone.utc).isoformat()})
        row = {"session_id": "synthetic-session", "transcript_path": str(transcript), "cost": {"total_api_duration_ms": tick},
            "rate_limits": {"five_hour": {"used_percentage": short, "resets_at": self.now + 1000},
                            "seven_day": {"used_percentage": used, "resets_at": self.now + 302400}}}
        self.q.capture_claude(row, self.now if now is None else now)

    def select(self, policy="quota-aware"):
        return self.q.select(self.now, policy)

    def test_select_claude_when_codex_ahead(self):
        self.event(60)
        self.assertEqual(self.select()["kind"], "claude")

    def test_default_and_close_gaps_prefer_codex(self):
        self.assertEqual(self.select()["kind"], "codex")
        self.event(59)
        self.capture(55, tick=101)
        self.assertEqual(self.select()["kind"], "codex")
        self.event(65)
        self.assertEqual(self.select()["kind"], "claude")

    def test_both_near_limit_block(self):
        self.event(99)
        self.capture(90, tick=101)
        self.assertIsNone(self.select()["kind"])

    def test_unknown_usage(self):
        self.event(60, age=601)
        self.assertEqual(self.select()["kind"], "claude")
        snap = self.q.read_json(self.q.state_dir() / "claude.json")
        snap["observed_at"] = self.now - 601
        self.write("state/agent-quota/claude.json", snap)
        self.assertIsNone(self.select()["kind"])

    def test_codex_only(self):
        self.event(100, credits=True)
        self.assertEqual(self.select("codex-only")["kind"], "codex")
        self.assertIsNone(self.select("typo")["kind"])

    def test_latest_event_not_mtime(self):
        old = self.event(99, age=100, file="z.jsonl")
        os.utime(old, (self.now + 9999,) * 2)
        self.assertEqual(self.select()["codex"]["week"], 40)
        # A newer malformed observation invalidates older usable limits.
        self.event(101)
        self.assertFalse(self.select()["codex"]["eligible"])

    def test_billing_unknown_blocks(self):
        self.event(60, credits=True)
        self.write(".claude.json", {"oauthAccount": {"accountUuid": "test-claude", "hasExtraUsageEnabled": True}})
        self.assertIsNone(self.select()["kind"])
        self.write(".claude.json", {"oauthAccount": {"accountUuid": "test-claude"}})
        self.event(20, credits=None)
        self.assertIsNone(self.select()["kind"])

    def test_identity_reset_and_nonfinite(self):
        self.write(".claude.json", {"oauthAccount": {"accountUuid": "other", "hasExtraUsageEnabled": False}})
        self.event(99)
        self.assertIsNone(self.select()["kind"])
        self.event(float("nan"))
        self.assertFalse(self.select()["codex"]["eligible"])
        self.event(20, reset=self.now - 1)
        self.assertFalse(self.select()["codex"]["eligible"])
        self.event(20, age=-100)
        self.assertFalse(self.select()["codex"]["eligible"])

    def test_statusline_repaint_does_not_refresh(self):
        self.capture(now=self.now + 900)
        snap = self.q.read_json(self.q.state_dir() / "claude.json")
        self.assertEqual(snap["observed_at"], self.now)
        self.capture(tick=101, now=self.now + 901)
        snap = self.q.read_json(self.q.state_dir() / "claude.json")
        self.assertEqual(snap["observed_at"], self.now + 901)
        self.assertEqual((self.q.state_dir() / "claude.json").stat().st_mode & 0o777, 0o600)

    def test_account_login_invalidates_codex_history(self):
        os.utime(self.home / ".codex/auth.json", (self.now + 1,) * 2)
        self.assertFalse(self.select()["codex"]["eligible"])

    def test_api_key_override_blocks_automatic_routing(self):
        os.environ["ANTHROPIC_API_KEY"] = "fake"
        os.environ["OPENAI_API_KEY"] = "fake"
        self.assertIsNone(self.select()["kind"])

    def test_boundary_limits(self):
        self.event(99)
        self.capture(80, short=79.9, tick=101)
        self.assertEqual(self.select()["kind"], "claude")
        self.capture(80, short=80, tick=102)
        self.assertIsNone(self.select()["kind"])


if __name__ == "__main__":
    unittest.main()
