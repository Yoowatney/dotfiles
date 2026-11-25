"""Public, synthetic quota fixtures: never read the operator's real account."""
import datetime
import importlib.machinery
import importlib.util
import json
import os
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("agent-quota")
sys.dont_write_bytecode = True


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        previous_cwd = Path.cwd()
        os.chdir(self.home)
        self.addCleanup(os.chdir, previous_cwd)
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

    def event(self, used=40, short=20, age=0, credits=False, file="a.jsonl", reset=None, short_reset=None):
        at = self.now - age
        row = {"timestamp": datetime.datetime.fromtimestamp(at, datetime.timezone.utc).isoformat(),
            "type": "event_msg", "payload": {"type": "token_count", "rate_limits": {
                "limit_id": "codex", "primary": {"used_percent": short, "window_minutes": 300, "resets_at": self.now + 1000 if short_reset is None else short_reset},
                "secondary": {"used_percent": used, "window_minutes": 10080, "resets_at": reset or self.now + 302400},
                "credits": {"has_credits": credits, "unlimited": False}}}}
        path = self.write(".codex/sessions/" + file, row)
        os.utime(path, (self.now,) * 2)
        return path

    def capture(self, used=40, short=20, tick=100, now=None, response_at=None):
        stamp = self.now if now is None else now
        transcript = self.home / "transcript.jsonl"
        if not transcript.exists() or tick != 100:
            self.write("transcript.jsonl", {"type": "assistant", "timestamp": datetime.datetime.fromtimestamp(stamp if response_at is None else response_at, datetime.timezone.utc).isoformat()})
        row = {"session_id": "synthetic-session", "transcript_path": str(transcript), "cost": {"total_api_duration_ms": tick},
            "rate_limits": {"five_hour": {"used_percentage": short, "resets_at": self.now + 1000},
                            "seven_day": {"used_percentage": used, "resets_at": self.now + 302400}}}
        self.q.capture_claude(row, self.now if now is None else now)

    def select(self, policy="quota-aware"):
        return self.q.select(self.now, policy)

    def test_select_claude_when_codex_ahead(self):
        self.event(60)
        self.assertEqual(self.select()["kind"], "claude")

    def test_week_only_record_from_local_codex_shape(self):
        # Real field shape; timestamp/reset replaced with the synthetic clock.
        row = json.loads(SCRIPT.with_name("fixtures").joinpath("codex-week-only.json").read_text())
        path = self.write(".codex/sessions/a.jsonl", row)
        os.utime(path, (self.now,) * 2)
        result = self.select()
        self.assertEqual(result["kind"], "codex")
        self.assertEqual(result["codex"]["week"], 21)
        self.assertIsNone(result["codex"]["short"])
        row["payload"]["rate_limits"]["primary"]["used_percent"] = 99
        self.write(".codex/sessions/a.jsonl", row)
        os.utime(path, (self.now,) * 2)
        self.assertEqual(self.select()["kind"], "claude")

    def test_observation_written_during_scan_is_accepted(self):
        self.event(age=-4)
        self.assertEqual(self.select()["kind"], "codex")
        self.event(age=-100)
        self.assertIsNone(self.select()["kind"])

    def test_display_reads_only_recent_five_files(self):
        for n in range(30):
            self.event(90, age=100, file=f"old-{n}.jsonl")
            os.utime(self.home / f".codex/sessions/old-{n}.jsonl", (self.now - 100,) * 2)
        opened = []
        original = Path.open
        def tracked(path, *args, **kwargs):
            if path.suffix == ".jsonl":
                opened.append(path)
            return original(path, *args, **kwargs)
        with patch.object(Path, "open", tracked), patch.object(sys, "argv", [str(SCRIPT), "codex-week", str(self.now)]), patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(self.q.main(), 0)
        self.assertEqual(output.getvalue(), "40\t1800302400\t1800000000\n")
        self.assertLessEqual(len(opened), 5)

    def test_decisions_are_bounded_and_parseable(self):
        folder = self.q.state_dir()
        log = folder / "decisions.jsonl"
        log.write_text((json.dumps({"reason": "x" * 2000}) + "\n") * 1100)
        (folder / "policy").write_text("quota-aware\n")
        with patch.object(sys, "argv", [str(SCRIPT), "select"]), patch.object(self.q.time, "time", return_value=self.now), patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(self.q.main(), 0)
        self.assertLessEqual(log.stat().st_size, 1048576)
        rows = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual(rows[-1]["kind"], "codex")

    def test_decision_discards_interrupted_last_row(self):
        log = self.q.state_dir() / "decisions.jsonl"
        log.write_bytes(b'{"kind":"codex"}\n{"kind":')
        self.q.record_decision({"kind": "claude"}, self.now)
        rows = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual([row["kind"] for row in rows], ["codex", "claude"])

    def test_default_and_close_gaps_prefer_codex(self):
        self.assertEqual(self.select()["kind"], "codex")
        self.event(59)
        self.capture(55, tick=101)
        self.assertEqual(self.select()["kind"], "codex")
        self.event(65)
        self.assertEqual(self.select()["kind"], "claude")

    def claude_extra(self, value=True):
        self.write(".claude.json", {"oauthAccount": {"accountUuid": "test-claude", "hasExtraUsageEnabled": value}})

    def test_claude_first_prefers_claude_within_reserve(self):
        self.event(10)  # Codex has far more room; Claude is still first.
        self.capture(50, tick=101)
        result = self.select("claude-first")
        self.assertEqual(result["kind"], "claude")
        self.assertTrue(result["claude"]["eligible"])

    def test_claude_first_accepts_known_extra_usage_but_keeps_reserve(self):
        self.claude_extra(True)
        self.capture(50, tick=101)
        self.assertEqual(self.select("claude-first")["kind"], "claude")
        self.assertIsNone(self.select("quota-aware")["claude"].get("week"), "quota-aware still excludes paid Claude")
        self.capture(80.5, tick=102)  # past the weekly reserve: back to Codex
        result = self.select("claude-first")
        self.assertEqual(result["kind"], "codex")
        self.assertIn("reserve", result["reason"])
        self.capture(50, short=80, tick=103)  # five-hour reserve reached
        self.assertEqual(self.select("claude-first")["kind"], "codex")

    def test_claude_first_still_requires_known_extra_usage_and_subscription(self):
        self.write(".claude.json", {"oauthAccount": {"accountUuid": "test-claude"}})
        self.assertEqual(self.select("claude-first")["kind"], "codex")
        self.claude_extra(True)
        os.environ["ANTHROPIC_API_KEY"] = "fake"
        self.assertEqual(self.select("claude-first")["kind"], "codex")
        del os.environ["ANTHROPIC_API_KEY"]
        self.write(".claude/settings.json", {"env": {"ANTHROPIC_API_KEY": "synthetic"}})
        self.assertEqual(self.select("claude-first")["kind"], "codex")

    def test_claude_first_stale_or_missing_usage_falls_back_to_codex(self):
        snap = self.q.read_json(self.q.state_dir() / "claude.json")
        snap["observed_at"] = self.now - 601
        self.write("state/agent-quota/claude.json", snap)
        self.assertEqual(self.select("claude-first")["kind"], "codex")
        (self.q.state_dir() / "claude.json").unlink()
        self.assertEqual(self.select("claude-first")["kind"], "codex")

    def test_claude_reserve_for_checks(self):
        # pane-spawn --check opens no check only for a measured reading past the reserve (exit 3); unknown is exit 1.
        def run():
            with patch.object(sys, "argv", [str(SCRIPT), "claude-reserve", str(self.home)]), patch.object(self.q.time, "time", return_value=self.now), patch("sys.stdout", new_callable=io.StringIO) as output:
                code = self.q.main()
            return code, json.loads(output.getvalue())
        self.capture(50, tick=101)
        self.assertEqual(run()[0], 0)
        self.capture(80.5, tick=102)
        code, result = run()
        self.assertEqual((code, result["reason"], result["week"]), (3, "reserve reached", 80.5))
        self.capture(50, short=80, tick=103)
        self.assertEqual(run()[0], 3, "the five-hour reserve counts too")
        self.claude_extra(True)
        self.assertEqual(run()[0], 3, "a paid-extra account keeps the reserve")
        (self.q.state_dir() / "claude.json").unlink()
        code, result = run()
        self.assertEqual((code, result["eligible"]), (1, False), "no reading is unknown, never past the reserve")
        self.assertNotEqual(result["reason"], "reserve reached")

    def test_claude_first_without_a_five_hour_window(self):
        # Claude Code reports no five-hour window when none is active.
        snap = self.q.read_json(self.q.state_dir() / "claude.json")
        snap["five_hour"] = None
        self.write("state/agent-quota/claude.json", snap)
        self.assertEqual(self.select("claude-first")["kind"], "claude")
        self.assertEqual(self.select("quota-aware")["claude"]["reason"], "missing limit window")

    def test_claude_first_neither_runner_refuses(self):
        self.event(99)
        self.capture(90, tick=101)
        self.assertIsNone(self.select("claude-first")["kind"])
        self.event(99, credits=True)
        self.capture(10, tick=102)
        self.assertEqual(self.select("claude-first")["kind"], "claude")

    def stale_codex(self, **kwargs):
        os.utime(self.home / ".codex/auth.json", (self.now - 2 * 86400,) * 2)
        self.capture(85, tick=101)
        return self.event(used=20, age=3600, **kwargs)

    def test_claude_first_reuses_low_stale_codex_in_same_week(self):
        self.stale_codex()
        result = self.select("claude-first")
        self.assertEqual(result["kind"], "codex")
        self.assertIn("stale", result["codex"]["reason"])
        self.assertIn("stale", result["reason"])
        self.assertIsNone(self.select("quota-aware")["kind"], "other policies keep the ten-minute limit")

    def test_stale_codex_age_and_weekly_headroom_boundaries(self):
        self.stale_codex()
        for age, used, allowed in [(600, 95, True), (600, 95.01, False), (601, 80, True), (86400, 80, True),
                                    (86401, 20, False), (601, 80.01, False), (-6, 20, False)]:
            with self.subTest(age=age, used=used):
                self.event(used=used, age=age, short_reset=self.now - age + 1000)
                self.assertEqual(self.select("claude-first")["kind"], "codex" if allowed else None)

    def test_stale_codex_requires_live_week_and_valid_original_windows(self):
        self.stale_codex()
        for reset in [self.now, self.now - 1, self.now + self.q.WEEK - 3599]:
            with self.subTest(reset=reset):
                self.event(age=3600, reset=reset)
                self.assertIsNone(self.select("claude-first")["kind"])
        self.event(age=3600, short_reset=self.now - 3600)
        self.assertIsNone(self.select("claude-first")["kind"], "invalid at observation is not an expired valid window")
        self.event(age=3600, short=None, short_reset=self.now - 1)
        self.assertIsNone(self.select("claude-first")["kind"], "expired malformed short window is still rejected")

    def test_stale_codex_short_window_limit_and_expiry(self):
        self.stale_codex()
        self.event(age=3600, short=80)
        self.assertIsNone(self.select("claude-first")["kind"])
        self.event(age=3600, short=99, short_reset=self.now)
        self.assertEqual(self.select("claude-first")["kind"], "codex")
        path = self.event(age=3600)
        row = json.loads(path.read_text())
        row["payload"]["rate_limits"]["primary"] = None
        path.write_text(json.dumps(row))
        os.utime(path, (self.now,) * 2)
        self.assertEqual(self.select("claude-first")["kind"], "codex")

    def test_stale_codex_keeps_login_billing_and_api_guards(self):
        self.stale_codex()
        auth = self.home / ".codex/auth.json"
        os.utime(auth, (self.now - 3599,) * 2)
        self.assertIn("predates", self.select("claude-first")["codex"]["reason"])
        os.utime(auth, (self.now - 7200,) * 2)
        for credit in [True, None, "false"]:
            self.event(age=3600, credits=credit)
            self.assertIn("paid credit", self.select("claude-first")["codex"]["reason"])
        self.event(age=3600)
        for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL"):
            with patch.dict(os.environ, {key: "synthetic"}):
                self.assertIn("API environment", self.select("claude-first")["codex"]["reason"])
        self.write(".codex/config.toml", {}).write_text('model_provider = "external"\n')
        self.assertIn("settings", self.select("claude-first")["codex"]["reason"])

    def test_claude_first_refusal_names_both_reasons(self):
        self.capture(85, tick=101)
        self.event(age=601, credits=True)
        result = self.select("claude-first")
        self.assertIsNone(result["kind"])
        self.assertIn("Claude: reserve reached", result["reason"])
        self.assertIn("Codex: paid credit availability", result["reason"])

    def test_stale_codex_does_not_fall_back_past_newer_unsafe_record(self):
        self.stale_codex()
        self.event(age=1000, credits=True, file="newer.jsonl")
        result = self.select("claude-first")
        self.assertIsNone(result["kind"])
        self.assertIn("paid credit", result["codex"]["reason"])

    def test_both_near_limit_block(self):
        self.event(99)
        self.capture(90, tick=101)
        self.assertIsNone(self.select()["kind"])

    def test_unknown_usage(self):
        self.event(60, age=601)
        self.assertIsNone(self.select()["kind"])
        snap = self.q.read_json(self.q.state_dir() / "claude.json")
        snap["observed_at"] = self.now - 601
        self.write("state/agent-quota/claude.json", snap)
        self.assertIsNone(self.select()["kind"])

    def test_unknown_claude_retains_known_safe_codex(self):
        (self.q.state_dir() / "claude.json").unlink()
        self.assertEqual(self.select()["kind"], "codex")

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

    def test_claude_api_settings_block_paid_routing(self):
        self.event(99)
        self.write(".claude/settings.json", {"env": {"ANTHROPIC_API_KEY": "synthetic"}})
        self.assertIsNone(self.select()["kind"])

    def test_codex_custom_provider_is_not_subscription(self):
        (self.home / ".codex/config.toml").write_text('model_provider = "custom"\n')
        self.assertIsNone(self.select()["kind"])

    def test_capture_rejects_old_initial_transcript(self):
        (self.q.state_dir() / "claude.json").unlink()
        self.capture(tick=101, response_at=self.now - 900)
        self.assertFalse((self.q.state_dir() / "claude.json").exists())
        self.capture(tick=102)
        self.assertEqual(self.q.read_json(self.q.state_dir() / "claude.json")["observed_at"], self.now)

    def agy_fixture(self, remaining=0.9, age=0, plan="Google AI Ultra"):
        self.write(".gemini/antigravity-cli/settings.json", {"useG1Credits": False, "modelProvider": ""})
        return self.write("state/agent-quota/agy.json", {
            "captured_at": self.now - age, "plan_tier": plan,
            "quota": {
                "gemini-5h": {"remaining_fraction": remaining, "reset_time": self.now + 1000},
                "gemini-weekly": {"remaining_fraction": 0.8, "reset_time": self.now + 302400},
                "3p-5h": {"remaining_fraction": 0, "reset_time": 0},
            }})

    def test_agy_only_uses_gemini_subscription_windows(self):
        self.agy_fixture()
        result = self.select("agy-only")
        self.assertEqual(result["kind"], "agy")
        self.assertAlmostEqual(result["agy"]["short"], 10)
        self.assertAlmostEqual(result["agy"]["week"], 20)

    def test_agy_reads_statusline_rfc3339_resets(self):
        path = self.agy_fixture()
        snap = json.loads(path.read_text())
        for key in ("gemini-5h", "gemini-weekly"):
            reset = snap["quota"][key]["reset_time"]
            snap["quota"][key]["reset_time"] = datetime.datetime.fromtimestamp(reset, datetime.timezone.utc).isoformat()
        path.write_text(json.dumps(snap))
        self.assertEqual(self.select("agy-only")["kind"], "agy")

    def test_agy_only_does_not_fall_back_on_stale_or_unknown_usage(self):
        for age in (601, -6):
            self.agy_fixture(age=age)
            self.assertIsNone(self.select("agy-only")["kind"])
        (self.home / "state/agent-quota/agy.json").unlink()
        self.assertIsNone(self.select("agy-only")["kind"])

    def test_agy_only_rejects_limit_and_invalid_fraction(self):
        for remaining in (0.2, 0, -0.1, 1.1, True, "0.9"):
            self.agy_fixture(remaining=remaining)
            self.assertIsNone(self.select("agy-only")["kind"], remaining)

    def test_agy_only_rejects_unknown_or_free_plan(self):
        for plan in (None, "", "Free", "Enterprise"):
            self.agy_fixture(plan=plan)
            self.assertIsNone(self.select("agy-only")["kind"])

    def test_agy_only_rejects_paid_or_adc_overrides(self):
        self.agy_fixture()
        for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "AGY_ADC_AUTH", "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT"):
            with patch.dict(os.environ, {key: "synthetic"}):
                self.assertIsNone(self.select("agy-only")["kind"], key)
        for settings in ({"useG1Credits": True}, {"useG1Credits": "false"}, {"useG1Credits": None}, {"modelProvider": None}, {"useG1Credits": False, "modelProvider": "gemini"}):
            self.write(".gemini/antigravity-cli/settings.json", settings)
            self.assertIsNone(self.select("agy-only")["kind"])

    def test_agy_accepts_native_pruned_default_settings(self):
        self.agy_fixture()
        self.write(".gemini/antigravity-cli/settings.json", {})
        self.assertEqual(self.select("agy-only")["kind"], "agy")

    def test_agy_rejects_unreadable_or_malformed_settings(self):
        self.agy_fixture()
        path = self.home / ".gemini/antigravity-cli/settings.json"
        for content in ("{", "[]", "null"):
            path.write_text(content)
            self.assertIsNone(self.select("agy-only")["kind"])
        path.unlink()
        self.assertIsNone(self.select("agy-only")["kind"])

    def test_agy_only_rejects_expired_windows(self):
        path = self.agy_fixture()
        for key in ("gemini-5h", "gemini-weekly"):
            data = json.loads(path.read_text())
            data["quota"][key]["reset_time"] = self.now
            path.write_text(json.dumps(data))
            self.assertIsNone(self.select("agy-only")["kind"])

    def test_agy_prepare_skips_fresh_cache_and_auth_overrides(self):
        self.agy_fixture()
        with patch.object(self.q, "refresh_agy_metadata") as refresh:
            self.q.prepare_agy(self.now, self.home)
            refresh.assert_not_called()
            with patch.dict(os.environ, {"GEMINI_API_KEY": "synthetic"}):
                self.q.prepare_agy(self.now + 700, self.home)
            refresh.assert_not_called()

    def test_agy_prepare_refreshes_missing_stale_or_incomplete_metadata(self):
        path = self.agy_fixture()
        with patch.object(self.q, "refresh_agy_metadata", return_value=False) as refresh:
            self.q.prepare_agy(self.now + 700, self.home)
            refresh.assert_called_once_with(self.home)
            path.unlink()
            self.q.prepare_agy(self.now, self.home)
            self.assertEqual(refresh.call_count, 2)
            self.write("state/agent-quota/agy.json", {"captured_at": self.now, "plan_tier": "", "quota": {}})
            self.q.prepare_agy(self.now, self.home)
            self.assertEqual(refresh.call_count, 3)

    def test_agy_refresh_uses_no_prompt_and_discards_terminal_output(self):
        self.agy_fixture()
        binary = self.home / ".local/bin/agy"
        binary.parent.mkdir(parents=True)
        binary.touch()
        process = unittest.mock.Mock()
        process.poll.return_value = None
        process.wait.return_value = 0
        with patch.object(self.q.subprocess, "Popen", return_value=process) as popen, \
             patch.object(self.q.pty, "openpty", return_value=(100, 101)), \
             patch.object(self.q.os, "close"), \
             patch.object(self.q.select_io, "select", return_value=([], [], [])), \
             patch.object(self.q.time, "monotonic", side_effect=[0, 20]):
            self.assertFalse(self.q.refresh_agy_metadata(self.home))
        args = popen.call_args.args[0]
        self.assertNotIn("-p", args)
        self.assertNotIn("--prompt", args)
        settings = json.loads(args[args.index("--settings") + 1])
        self.assertIs(settings["useG1Credits"], False)
        self.assertEqual(settings["toolPermission"], "proceed-in-sandbox")
        self.assertEqual(popen.call_args.kwargs["stdin"], 101)
        self.assertIs(popen.call_args.kwargs["start_new_session"], False)
        process.terminate.assert_called_once()

    def test_agy_refresh_does_not_accept_initial_empty_statusline(self):
        path = self.agy_fixture(plan=None)
        os.utime(path, (self.now, self.now))
        binary = self.home / ".local/bin/agy"
        binary.parent.mkdir(parents=True)
        binary.touch()
        process = unittest.mock.Mock()
        process.poll.return_value = None
        process.wait.return_value = 0
        with patch.object(self.q.subprocess, "Popen", return_value=process), \
             patch.object(self.q.pty, "openpty", return_value=(100, 101)), \
             patch.object(self.q.os, "close"), \
             patch.object(self.q.select_io, "select", return_value=([], [], [])), \
             patch.object(self.q.time, "time", return_value=self.now), \
             patch.object(self.q.time, "monotonic", side_effect=[0, 1, 20]):
            self.assertFalse(self.q.refresh_agy_metadata(self.home))


if __name__ == "__main__":
    unittest.main()
