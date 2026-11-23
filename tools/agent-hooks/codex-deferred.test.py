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
import unicodedata
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
        for name in ("task-ledger.py", "codex-deferred.py", "herdr_peers.py", "check-permissions.py"):
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
                         "'pane get') cat \"$TEST_PANE_GET\" ;;\n"
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

    def current_pane(self, terminal):
        # pane get resolves the old environment id after a workspace move.
        self.env["HERDR_PANE_ID"] = "w9:p8"
        self.env["TEST_PANE_GET"] = str(self.root / "pane-get.json")
        Path(self.env["TEST_PANE_GET"]).write_text(json.dumps({"result": {"pane": {
            "pane_id": "w1:p1", "terminal_id": terminal}}}))

    def test_main_stop_does_not_capture_quota_report(self):
        self.main_pane()
        self.current_pane("term_TEST")
        self.defer("Codex quota exhausted; cross-review deferred.")
        self.assertFalse((self.root / "deferred/state.json").exists())

    def test_worker_stop_still_captures_the_same_quota_report(self):
        self.main_pane()
        self.current_pane("term_WORKER")
        self.defer("Codex quota exhausted; cross-review deferred.")
        item = self.state()["items"][0]
        self.assertEqual(item["status"], "pending")
        self.assertEqual(item["summary"], "Codex quota exhausted; cross-review deferred.")

    def test_failed_pane_lookup_does_not_drop_worker_deferral(self):
        self.main_pane()
        self.current_pane("term_WORKER")
        Path(self.env["TEST_PANE_GET"]).unlink()
        self.defer()
        self.assertEqual(self.state()["items"][0]["status"], "pending")

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

    def test_nov22_daily_review_html_does_not_register_deferred_work(self):
        # Sanitized reconstruction: the stored summary is truncated at 6000
        # characters and the original ephemeral transcript no longer exists.
        report = ("<!doctype html>\n<html lang=\"ko\">\n<head>"
                  "<title>Daily review 2026-11-22</title></head>\n<body>\n\n"
                  "<section><h2>Completed checks</h2>\n"
                  "<p>Codex quota exhausted; review deferred earlier.</p>\n"
                  "<p>The review has since finished; these are historical findings.</p>"
                  "</section>\n</body>\n</html>")
        self.assert_document_not_deferred(report)

    def test_nov23_daily_review_html_does_not_register_deferred_work(self):
        # Reconstruct unrelated usage and failure wording within the saved
        # document structure; the truncated summary omits the original trigger.
        report = ("<!doctype html>\n<html lang=\"ko\">\n<head>"
                  "<title>Daily review 2026-11-23</title></head>\n<body>\n\n"
                  "<div class=\"hero\">\n"
                  "<p>코덱스 기록에서 경로 검사를 확인했습니다.</p>\n"
                  "<p>클로드 주간 사용량은 94%입니다.</p>\n"
                  "<p>아침 점검 결과를 전하지 못했습니다.</p>\n"
                  "</div>\n</body>\n</html>")
        self.assert_document_not_deferred(report)

    def assert_document_not_deferred(self, report):
        for runtime in ("claude", "codex"):
            with self.subTest(runtime=runtime):
                if runtime == "claude":
                    rows = [{"type": "assistant", "message": {"content": report}}]
                else:
                    self.success(write_only=True)
                    rows = [json.loads(line) for line in self.transcript.read_text().splitlines()]
                    rows[-1]["payload"]["content"][0]["text"] = report
                self.transcript.write_text("".join(json.dumps(row) + "\n" for row in rows))
                self.hook("SessionStart")
                self.hook(transcript_path=str(self.transcript))
                self.hook(transcript_path=str(self.transcript))  # Already open session.
                self.assertFalse((self.root / "deferred/state.json").exists())

    def test_long_report_does_not_join_unrelated_quota_and_failure_wording(self):
        report = ("Daily review\n\n" + "The requested checks finished successfully. " * 40 +
                  "Codex generated the report. Usage limits were inspected. "
                  "An unrelated deployment was deferred for a failing test.")
        self.assert_document_not_deferred(report)

    def test_review_report_quoted_deferral_examples_are_not_current_work(self):
        # Sanitized excerpt of the review report that was itself miscaptured.
        report = ("The reviewed change misses genuine quota deferrals.\n\n"
                  "## Direct comparison\n\n"
                  "| Input | Before | After |\n|---|---|---|\n"
                  "| `Codex hit its usage limit; review deferred.` | True | False |\n"
                  "| `Codex 사용량 때문에 교차 리뷰를 미룹니다.` | True | False |\n\n"
                  "- Existing record: `Codex 사용량이 6%만 남아서 보완을 미뤘습니다.`\n\n"
                  "TASK_CHECK: fail\n"
                  "남은 것: 요유 몫: 없음 · 유요 몫: 수정 뒤 누락을 확인함 · 세션: 검토 중")
        self.assert_document_not_deferred(report)

    def test_html_tag_example_does_not_hide_a_real_deferral(self):
        for example in ('<html lang="ko">', '`<html lang="ko">`'):
            with self.subTest(example=example):
                text = (f'보고서의 {example} 속성을 고쳤습니다.\n\n'
                        'Codex 사용량이 없어 교차 리뷰를 미룹니다.')
                self.defer(text)
                self.assertTrue((self.root / "deferred/state.json").exists())
                self.assertEqual(self.state()["items"][0]["summary"], text)

    def test_available_quota_and_unrelated_failure_are_not_a_deferral(self):
        self.hook(last_assistant_message="Codex 사용량은 충분하지만 보고서 파일은 찾지 못했습니다.")
        self.assertFalse((self.root / "deferred/state.json").exists())

    def test_causal_quota_deferrals_keep_existing_natural_language_variants(self):
        for text in ("Codex 사용량 때문에 검토를 미룹니다.",
                     "Codex 주간 사용량이 88%여서 검토를 미룹니다.",
                     "Codex 사용량이 3%만 남아서 검토를 미룹니다.",
                     "Codex quota ran out; cross-review deferred.",
                     "Codex hit its usage limit; cross-review deferred."):
            with self.subTest(text=text):
                self.defer(text)
                self.assertTrue((self.root / "deferred/state.json").exists())
                self.assertEqual(self.state()["items"][0]["summary"], text)

    def test_reached_quota_limit_variants_are_captured(self):
        for text in ("Codex 주간 사용량 한도에 도달해 리뷰를 미룹니다.",
                     "Codex 사용량 한도를 다 써서 리뷰를 내일로 미룹니다.",
                     "Codex 사용량 한도 도달로 교차 리뷰를 보류합니다.",
                     "Codex 주간 사용량 한도가 찼습니다. 교차 리뷰는 내일로 미룹니다.",
                     "Codex usage limit hit; review deferred."):
            with self.subTest(text=text):
                self.defer(text)
                self.assertTrue((self.root / "deferred/state.json").exists())
                self.assertEqual(self.state()["items"][0]["summary"], text)

    def test_usage_explanations_do_not_imply_a_quota_obstacle(self):
        for text in ("Codex 사용량이 충분히 남아서 교차 리뷰를 돌렸지만, 보고서 파일은 찾지 못했습니다.",
                     "Codex 사용량 정책에 따라서 리뷰는 Claude로 넘겼고, 배포는 테스트 실패로 보류합니다.",
                     "Codex reviewed the architecture; usage limits unchanged. Deployment deferred for a failing test.",
                     "Codex 사용량은 문제 없습니다. 배포는 테스트 실패로 보류합니다.",
                     "Codex 사용량 표시가 늦게 갱신되기 때문에 아침 점검 결과를 전하지 못했습니다."):
            with self.subTest(text=text):
                (self.root / "deferred/state.json").unlink(missing_ok=True)
                self.hook(last_assistant_message=text)
                self.assertFalse((self.root / "deferred/state.json").exists())

    def test_html_does_not_hide_a_real_deferral_outside_the_document(self):
        self.defer("<HTML><body>Completed report.</body></HTML>\n\n"
                   "Codex quota exhausted; cross-review deferred.")
        self.assertEqual(self.state()["items"][0]["status"], "pending")

    def test_partial_html_is_not_a_current_quota_error(self):
        self.assert_document_not_deferred("<!doctype html><html><body>"
                                          "Codex quota exhausted; historical review deferred.")

    def test_available_nonzero_quota_does_not_match_zero_percent(self):
        self.hook(last_assistant_message="Codex 주간 사용량은 10% 남았고 보고서 파일은 찾지 못했습니다.")
        self.assertFalse((self.root / "deferred/state.json").exists())

    def test_long_report_does_not_hide_a_real_deferral_at_the_end(self):
        self.defer("Completed checks. " * 500 + "\n\n"
                   "Codex 사용량이 없어 교차 리뷰를 미룹니다.")
        self.assertEqual(self.state()["items"][0]["status"], "pending")

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

    def test_finished_reports_are_not_captured_from_either_runtime(self):
        report = ("작업 일지를 기록했습니다. Codex 사용량 때문에 중단됐던 확인도 마쳤습니다.\n\n"
                  "남은 것: 요유 몫: 없음 · 유요 몫: 없음 · 세션: 끝남, 백그라운드 작업 없음")
        for runtime in ("claude", "codex"):
            with self.subTest(runtime=runtime):
                if runtime == "claude":
                    rows = [{"type": "assistant", "message": {"content": report}}]
                else:
                    self.success(write_only=True)
                    rows = [json.loads(line) for line in self.transcript.read_text().splitlines()]
                    rows[-1]["payload"]["content"][0]["text"] = report
                self.transcript.write_text("".join(json.dumps(row) + "\n" for row in rows))
                self.hook("SessionStart")
                self.hook(transcript_path=str(self.transcript))
                self.hook(transcript_path=str(self.transcript))  # An already open session's next Stop.
                self.assertFalse((self.root / "deferred/state.json").exists())

    def test_explicit_empty_finished_footer_overrides_historical_quota_words(self):
        for footer in (
                "남은 것: 요유 몫: 없음 · 유요 몫: 없음 · 세션: 닫아도 됨",
                "**남은 것:** 요유 몫: 없음 · 유요 몫: 없음 · 세션: 닫음",
                "남은 것:\n- 요유 몫: 없음\n- 유요 몫: 없음\n- 세션: 끝남, 실행 중 작업 없음",
                unicodedata.normalize("NFD", "남은 것: 요유 몫: 없음 · 유요 몫: 없음 · 세션: 끝남")):
            with self.subTest(footer=footer):
                self.defer("Codex quota exhausted; review deferred earlier.\n\n" + footer)
                self.assertFalse((self.root / "deferred/state.json").exists())

    def test_incomplete_or_quoted_finished_footer_does_not_hide_deferral(self):
        finished = "남은 것: 요유 몫: 없음 · 유요 몫: 없음 · 세션: 닫아도 됨"
        for footer in (
                "남은 것: 요유 몫: 승인 · 유요 몫: 없음 · 세션: 닫아도 됨",
                "남은 것: 요유 몫: 없음 · 유요 몫: 리뷰 보류 · 세션: 닫아도 됨",
                "남은 것: 요유 몫: 없음 · 유요 몫: 없음 · 세션: 닫으면 감시 멈춤",
                "남은 것: 요유 몫: 없음 · 세션: 닫아도 됨",
                "> " + finished,
                "```\n" + finished + "\n```",
                finished + "\n\n다음 확인은 보류합니다.",
                finished + "\n남은 것: 요유 몫: 없음 · 유요 몫: 확인 보류 · 세션: 닫으면 확인 멈춤"):
            with self.subTest(footer=footer):
                self.defer("Codex quota exhausted; review deferred.\n\n" + footer)
                self.assertEqual(self.state()["items"][0]["status"], "pending")

    def test_finished_report_never_resolves_or_replaces_existing_pending_work(self):
        self.defer()
        original = self.state()["items"][0]
        self.success(write_only=True)
        self.hook(transcript_path=str(self.transcript), last_assistant_message=(
            "Codex quota exhausted; review deferred earlier.\n\n"
            "남은 것: 요유 몫: 없음 · 유요 몫: 없음 · 세션: 끝남"))
        self.assertEqual(self.state()["items"], [original])
        self.assertEqual(self.state()["recovery"]["provider"], "openai")

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
