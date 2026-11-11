#!/usr/bin/env python3
"""Exercise deferred-work capture and recovery through the shared hook entry point."""
import concurrent.futures
import datetime as dt
import json
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).parent
SCRIPT = ROOT / "codex-deferred.py"
HOOK = ROOT / "task-ledger.py"


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        # Copy the entry points: even an accidental real-main lookup cannot
        # reach the production sibling pane-tell executable from these tests.
        self.hooks = self.root / "hooks"
        self.hooks.mkdir()
        for name in ("task-ledger.py", "codex-deferred.py"):
            shutil.copyfile(ROOT / name, self.hooks / name)
        self.hook_script = self.hooks / "task-ledger.py"
        self.script = self.hooks / "codex-deferred.py"
        relay = self.hooks / "pane-tell"
        relay.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\" >> \"$TEST_RELAY_LOG\"\n"
                         "[ ! -e \"$TEST_RELAY_FAIL\" ] || exit 3\nprintf 'queued for test-only\\n'\n")
        relay.chmod(0o700)
        herdr = self.root / "herdr"
        herdr.write_text("#!/bin/sh\ncase \"$1 $2\" in\n"
                         "'session list') printf '%s' '{\"sessions\":[{\"name\":\"default\",\"running\":true,\"socket_path\":\"test.sock\"}]}' ;;\n"
                         "'pane list') printf '%s' '{\"result\":{\"panes\":[{\"pane_id\":\"w1:p1\",\"terminal_id\":\"term_TEST\"}]}}' ;;\n"
                         "*) exit 2 ;; esac\n")
        herdr.chmod(0o700)
        self.env = dict(os.environ, CODEX_DEFERRED_DIR=str(self.root / "deferred"),
                        AGENT_TASK_LEDGER_DIR=str(self.root / "accounting"),
                        XDG_STATE_HOME=str(self.root / "state"), HERDR_BIN_PATH=str(herdr),
                        TEST_RELAY_LOG=str(self.root / "relay.log"), TEST_RELAY_FAIL=str(self.root / "relay.fail"))
        for key in ("HERDR_TASK_ID", "HERDR_PARENT_TASK_ID", "HERDR_PANE_ID"):
            self.env.pop(key, None)
        self.transcript = self.root / "codex.jsonl"

    def run_script(self, script, *args, payload=None, drain=True):
        script = self.script if script == SCRIPT else self.hook_script
        result = subprocess.run([sys.executable, str(script), *args], env=self.env,
                                input=json.dumps(payload or {}), text=True, capture_output=True, timeout=10 if not drain else 60)
        self.assertEqual(result.returncode, 0, result.stderr)
        if script == self.hook_script and drain:
            # The real hook detaches delivery so a slow receiver cannot consume
            # its 10s budget. Drain the notifier before asserting its receipt.
            self.run_script(SCRIPT, "notify", "--wait")
        return result.stdout

    def hook(self, event="Stop", drain=True, **payload):
        return self.run_script(HOOK, "hook", event, payload={"session_id": "session", **payload}, drain=drain)

    def state(self):
        return json.loads((self.root / "deferred/state.json").read_text())

    def defer(self, text="Codex 사용량이 없어 교차 리뷰를 미룹니다. https://example.org/issues/1"):
        self.hook(last_assistant_message=text)

    def success(self, age=0, error=False, final=True, drain=True, write_only=False, phase="final"):
        stamp = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=age)).isoformat()
        rows = [
            {"type": "session_meta", "payload": {"id": "session", "model_provider": "openai"}},
            {"timestamp": stamp, "type": "event_msg", "payload": {"type": "task_started"}},
            {"timestamp": stamp, "type": "response_item", "payload": {
                "type": "message", "role": "assistant", "phase": phase if final else "commentary",
                "content": [{"type": "output_text", "text": "Checked the requested files."}]}},
        ]
        if error:
            rows.append({"timestamp": stamp, "type": "event_msg", "payload": {
                "type": "task_complete", "error": {"codex_error_info": "usage_limit_exceeded"}}})
        self.transcript.write_text("".join(json.dumps(r) + "\n" for r in rows))
        if not write_only:
            self.hook(transcript_path=str(self.transcript), drain=drain)

    def test_open_claude_stop_captures_without_task_accounting_id(self):
        self.defer()
        item = self.state()["items"][0]
        self.assertEqual(item["status"], "pending")
        self.assertIn("https://example.org/issues/1", item["links"])
        self.assertIsNone(item.get("notified_at"))

    def test_current_codex_final_answer_phase_proves_recovery(self):
        # Codex 0.167.0 writes response_item messages with final_answer, as
        # observed in an actual waiting session before this observer was installed.
        self.main_pane()
        self.defer()
        self.success(phase="final_answer")
        item = self.state()["items"][0]
        self.assertIsNotNone(item.get("queued_at"))
        self.assertEqual(item["recovery_evidence"]["session_id"], "session")
        self.assertIn("Codex 실제 답변 성공", (self.root / "relay.log").read_text())

    def test_missing_or_malformed_transcript_does_not_hide_payload_deferral(self):
        for malformed in (False, True):
            with self.subTest(malformed=malformed):
                if malformed:
                    self.transcript.write_text("not json\n")
                self.hook(transcript_path=str(self.transcript),
                          last_assistant_message="Codex quota exhausted; review deferred.")
                self.assertEqual(self.state()["items"][0]["status"], "pending")

    def test_historical_stop_replay_does_not_reopen_resolved_changed_work(self):
        first = "Codex quota exhausted; first review deferred."
        self.defer(first)
        self.defer("Codex quota exhausted; second review deferred.")
        self.run_script(SCRIPT, "resolve", "--id", "session", "--reason", "Main handled both reviews")
        self.defer(first)
        item = self.state()["items"][0]
        self.assertEqual(item["status"], "resolved")
        self.assertIn("second review", item["summary"])

    def test_slow_accepted_delivery_is_not_repeated(self):
        self.main_pane()
        relay = self.hooks / "pane-tell"
        relay.write_text("#!/bin/sh\nprintf 'accepted\\n' >> \"$TEST_RELAY_LOG\"\n"
                         "sleep 3\nprintf 'queued for test-only\\n'\n")
        self.defer()
        self.success()
        self.hook("SessionStart")
        self.assertIsNotNone(self.state()["items"][0].get("queued_at"))
        self.assertEqual((self.root / "relay.log").read_text().splitlines(), ["accepted"])

    def test_pending_receipt_does_not_block_hooks_or_acknowledge_changed_work(self):
        self.main_pane()
        relay = self.hooks / "pane-tell"
        relay.write_text("#!/bin/sh\nprintf 'accepted\\n' >> \"$TEST_RELAY_LOG\"\n"
                         "while [ ! -e \"$TEST_RELAY_FAIL\" ]; do sleep 0.05; done\n"
                         "printf 'queued for test-only\\n'\n")
        release = self.root / "relay.fail"
        try:
            self.defer()
            self.success(drain=False)
            deadline = time.monotonic() + 15
            while not (self.root / "relay.log").exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue((self.root / "relay.log").exists(), "notifier did not start")
            self.hook(last_assistant_message="Codex quota exhausted; newer review deferred.", drain=False)
            self.assertIn("newer review", self.state()["items"][0]["summary"])
        finally:
            release.touch()
            self.run_script(SCRIPT, "notify", "--wait")
        self.assertIsNone(self.state()["items"][0].get("queued_at"), "old receipt must not acknowledge new work")
        self.assertEqual((self.root / "relay.log").read_text().splitlines(), ["accepted"])

    def test_fresh_recovery_during_pending_receipt_is_drained_without_another_hook(self):
        self.main_pane()
        (self.hooks / "pane-tell").write_text(
            "#!/bin/sh\nprintf 'accepted\\n' >> \"$TEST_RELAY_LOG\"\n"
            "while [ ! -e \"$TEST_RELAY_FAIL\" ]; do sleep 0.05; done\n"
            "printf 'queued for test-only\\n'\n")
        release = self.root / "relay.fail"
        spec = importlib.util.spec_from_file_location("recovery_under_test", self.script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        # Control worker scheduling: the second attempt must lose the lock
        # before the first receipt arrives, rather than happen to start later.
        with mock.patch.dict(os.environ, self.env, clear=True), mock.patch.object(module, "start_notifier"):
            module.observe("Stop", {"session_id": "session", "last_assistant_message": "Codex quota exhausted; first review deferred."})
            self.success(write_only=True)
            module.observe("Stop", {"session_id": "session", "transcript_path": str(self.transcript)})
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(module.notify_pending)
                try:
                    deadline = time.monotonic() + 15
                    while not (self.root / "relay.log").exists() and time.monotonic() < deadline:
                        time.sleep(0.05)
                    self.assertTrue((self.root / "relay.log").exists(), "notifier did not start")
                    module.observe("Stop", {"session_id": "session", "last_assistant_message": "Codex quota exhausted; newer review deferred."})
                    self.success(write_only=True)
                    module.observe("Stop", {"session_id": "session", "transcript_path": str(self.transcript)})
                    module.notify_pending()
                finally:
                    release.touch()
                pending.result(timeout=15)
            self.assertIsNotNone(self.state()["items"][0].get("queued_at"))
            self.assertEqual((self.root / "relay.log").read_text().splitlines(), ["accepted", "accepted"])

    def test_claude_transcript_fallback_and_codex_final_are_both_captured(self):
        self.transcript.write_text(json.dumps({"type": "assistant", "message": {
            "content": [{"type": "text", "text": "Codex quota exhausted; cross-review deferred."}]}}) + "\n")
        self.hook(transcript_path=str(self.transcript))
        self.assertEqual(len(self.state()["items"]), 1)
        self.defer("Codex 사용량이 막혀 리뷰 보류")
        self.assertEqual(len(self.state()["items"]), 1, "one pending task per job/session")

    def test_new_session_start_does_not_claim_recovery(self):
        self.defer()
        self.hook("SessionStart", transcript_path=str(self.transcript))
        self.assertIsNone(self.state().get("recovery"))

    def test_real_codex_answer_marks_candidates_but_never_restarts_or_resolves(self):
        self.defer()
        self.success()
        state = self.state()
        self.assertEqual(state["recovery"]["session_id"], "session")
        self.assertEqual(state["items"][0]["status"], "pending")
        self.assertIsNone(state["items"][0].get("notified_at"), "no main pane: retain durable pending notice")

    def test_stale_answer_error_and_commentary_do_not_imply_recovery(self):
        for kwargs in ({"age": 900}, {"error": True}, {"final": False}):
            with self.subTest(kwargs=kwargs):
                self.defer()
                self.success(**kwargs)
                self.assertIsNone(self.state().get("recovery"))

    def test_non_codex_output_and_ordinary_usage_discussion_are_not_deferrals(self):
        self.hook(last_assistant_message="Codex 사용량을 확인했습니다. 주간 95% 남음.")
        self.assertFalse((self.root / "deferred/state.json").exists())
        self.hook(last_assistant_message="Claude quota exhausted; review deferred.")
        self.assertFalse((self.root / "deferred/state.json").exists())

    def test_explicit_record_resolve_and_concurrent_records_are_durable(self):
        def record(i):
            return self.run_script(SCRIPT, "defer", "--id", f"job-{i}", "--summary", "Review delayed",
                                   "--source", "report:local", "--link", "https://example.org/task")
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(record, list(range(8)) * 2))
        self.assertEqual(len(self.state()["items"]), 8)
        self.run_script(SCRIPT, "resolve", "--id", "job-1", "--reason", "Already assigned by main")
        self.assertEqual(next(i for i in self.state()["items"] if i["id"] == "job-1")["status"], "resolved")
        self.assertEqual((self.root / "deferred/state.json").stat().st_mode & 0o777, 0o600)

    def test_corrupt_store_is_not_overwritten_and_hook_fails_open(self):
        folder = self.root / "deferred"
        folder.mkdir()
        (folder / "state.json").write_text("broken")
        self.defer()
        self.assertEqual((folder / "state.json").read_text(), "broken")

    def test_fake_main_delivery_is_deduplicated_and_pending_work_is_preserved(self):
        self.main_pane()
        self.defer()
        self.success()
        self.assertIsNotNone(self.state()["items"][0].get("queued_at"))
        self.assertEqual(self.state()["items"][0]["status"], "pending")
        before = (self.root / "relay.log").read_text()
        self.assertIn("default/w1:p1", before)
        self.assertIn("example.org/issues/1", before)
        self.hook("SessionStart")
        self.assertEqual((self.root / "relay.log").read_text(), before)

    def main_pane(self):
        folder = self.root / "state/orchestrator"
        folder.mkdir(parents=True)
        (folder / "main").write_text("term_TEST")

    def test_failed_delivery_retries_on_new_session_start_without_losing_work(self):
        self.main_pane()
        (self.root / "relay.fail").touch()
        self.defer()
        self.success()
        self.assertIsNone(self.state()["items"][0].get("queued_at"))
        (self.root / "relay.fail").unlink()
        self.hook("SessionStart")
        self.assertIsNotNone(self.state()["items"][0].get("queued_at"))

    def test_closed_main_notice_remains_until_main_becomes_available(self):
        self.defer()
        self.success()
        self.assertFalse((self.root / "relay.log").exists())
        self.main_pane()
        self.hook("SessionStart")
        self.assertIsNotNone(self.state()["items"][0].get("queued_at"))

    def test_resumption_reports_are_not_new_deferrals(self):
        for text in ("Codex quota was exhausted; deferred tasks have now all resumed.",
                     "Codex quota was exhausted. Deferred tasks have now all resumed.",
                     "Codex 사용량 한도로 중단됐던 작업을 재개했습니다."):
            self.hook(last_assistant_message=text)
            self.assertFalse((self.root / "deferred/state.json").exists())

    def test_unresumed_work_is_still_captured(self):
        self.defer("Codex quota exhausted; deferred tasks have not resumed.")
        self.assertEqual(self.state()["items"][0]["status"], "pending")

    def test_other_resumed_work_does_not_hide_current_deferral(self):
        self.defer("Codex 사용량 한도로 리뷰는 보류합니다. 다른 작업은 재개했습니다.")
        self.assertEqual(self.state()["items"][0]["status"], "pending")

    def test_reported_stop_wording_is_captured(self):
        self.defer("Codex 주간 사용량이 바닥나서 11/16까지 도구 일을 멈춥니다.")
        self.assertEqual(self.state()["items"][0]["status"], "pending")

    def test_quota_reason_for_tomorrow_assignment_is_captured(self):
        self.defer("보드 항목은 내일 10:00 자동 배정에 맡깁니다. 이번 주 Codex 사용량이 한 번 바닥났기 때문입니다.")
        self.assertEqual(self.state()["items"][0]["status"], "pending")

    def test_changed_deferral_after_notice_requires_new_recovery(self):
        self.main_pane()
        self.defer()
        self.success()
        self.assertIsNotNone(self.state()["items"][0].get("queued_at"))
        self.defer("Codex quota exhausted; another review deferred. https://example.org/issues/2")
        self.assertIsNone(self.state()["items"][0].get("queued_at"))
        self.assertIn("https://example.org/issues/1", self.state()["items"][0]["history"][0]["links"])
        self.success()
        self.assertIsNotNone(self.state()["items"][0].get("queued_at"))
        self.assertIn("example.org/issues/2", (self.root / "relay.log").read_text())

    def test_upstream_timeout_preserves_pending_notice(self):
        self.main_pane()
        self.defer()
        Path(self.env["HERDR_BIN_PATH"]).write_text("#!/bin/sh\nexec sleep 3\n")
        self.success()
        self.assertIsNone(self.state()["items"][0].get("queued_at"))
        self.assertFalse((self.root / "relay.log").exists())


if __name__ == "__main__":
    unittest.main()
