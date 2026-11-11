#!/usr/bin/env python3
"""Routing contracts: run finish(), replace only external herdr/process boundaries."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("auto", Path(__file__).with_name("auto-tab-close.py"))
auto = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(auto)

DONE = "pytest로 12개 통과를 확인했습니다.\n세부 근거는 보존됩니다.\n남은 것: 요유 몫: 없음 · 유요 몫: 없음 · 세션: 닫아도 됨"
CHECKED = "pytest를 다시 실행해 12개 통과를 봤습니다. PASS\n남은 것: 요유 몫: 없음 · 유요 몫: 없음 · 세션: 닫아도 됨"


class RelayTests(unittest.TestCase):
    def test_accounting_id_follows_worker_to_check_without_changing_receiver(self):
        self.setUp_accounting_id = 'job-test-123'
        self.env['HERDR_TASK_ID'] = self.setUp_accounting_id
        self.finish()
        args, env = self.spawns[0]
        self.assertEqual(args[args.index('--job-id') + 1], self.setUp_accounting_id)
        self.assertEqual(env['HERDR_PANE_ID'], self.main['pane_id'])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / "state/orchestrator"
        self.state.mkdir(parents=True)
        (self.state / "main").write_text("term_M")
        self.main = dict(pane_id="w5:p3", terminal_id="term_M", cwd="/main", label="🟢 👑 test receiver")
        self.worker = dict(pane_id="w1:p2", terminal_id="term_W", cwd="/repo with spaces", label="🟢 🫡 Job",
                           tab_id="w1:t1", workspace_id="w1", agent_session={"value": "sid-W"})
        self.checker = None
        self.names = {"w1:p2": "spawn-M-1"}
        self.tells, self.spawns, self.closed, self.alerts = [], [], [], []
        self.spawn_error = None
        self.tell_exit = 0
        self.env = dict(HOME=str(self.root), XDG_STATE_HOME=str(self.root / "state"),
                        HERDR_PANE_ID="w1:p2", HERDR_SOCKET_PATH="/sessions/workers/herdr.sock",
                        PANE_REPORT_DIR=str(self.root / "reports"), AUTO_TAB_CLOSE_PANE_SPAWN="/fake/pane-spawn",
                        AUTO_TAB_CLOSE_PANE_TELL="/fake/pane-tell", AUTO_TAB_CLOSE_PANE_LABEL="/fake/pane-label",
                        HERDR_ERROR_NOTIFY="/fake/herdr-error-notify")
        for target, value in (("herdr", self.herdr), ("subprocess.run", self.call_process),
                              ("herdr_peers.all_panes", self.all_panes), ("herdr_peers.socket_of", self.socket_of)):
            if "." in target:
                owner, attr = target.split(".")
                p = patch.object(getattr(auto, owner), attr, value)
            else:
                p = patch.object(auto, target, value)
            p.start()
            self.addCleanup(p.stop)

    def socket_of(self, env, name):
        return "/default/herdr.sock" if name == "default" else "/sessions/workers/herdr.sock"

    def all_panes(self, env):
        return {"term_M": ("default/w5:p3", self.main), "term_W": ("w1:p2", self.worker)}

    def herdr(self, env, *args, **kwargs):
        panes = [self.worker] + ([self.checker] if self.checker else [])
        if args[:2] == ("pane", "get"):
            return {"pane": next((p for p in [self.main, *panes] if p["pane_id"] == args[2]), {})}
        if args[:2] == ("pane", "list"):
            return {"panes": panes}
        if args[:2] == ("agent", "list"):
            return {"agents": [dict(pane_id=p, name=n) for p, n in self.names.items()]}
        if args[:2] == ("agent", "get"):
            return {"agent": {"name": self.names.get(args[2], "")}}
        raise AssertionError(args)

    def call_process(self, args, **kwargs):
        if args[0].endswith("pane-spawn"):
            self.spawns.append((args, kwargs["env"]))
            if self.spawn_error:
                if isinstance(self.spawn_error, Exception):
                    raise self.spawn_error
                return subprocess.CompletedProcess(args, 1, "", self.spawn_error)
            self.checker = dict(self.worker, pane_id="w1:p9", terminal_id="term_C", label="🟢 🫡 확인 " + args[-2],
                                agent_session={"value": "sid-C"})
            self.names["w1:p9"] = "spawn-M-chk9"
            return subprocess.CompletedProcess(args, 0, "workers/w1:p9\n", "")
        if args[0].endswith("pane-tell"):
            self.tells.append((args[1], args[2]))
            return subprocess.CompletedProcess(args, self.tell_exit)
        if args[0].endswith("pane-label"):
            self.closed.append(kwargs["env"]["HERDR_PANE_ID"])
            return subprocess.CompletedProcess(args, 0)
        if args[0] == "/fake/herdr-error-notify":
            self.alerts.append(args[1:])
            return subprocess.CompletedProcess(args, 0)
        raise AssertionError(args)

    def finish(self, text=DONE, check=False):
        auto.finish(text, {**self.env, "HERDR_PANE_ID": "w1:p9" if check else "w1:p2"})

    def transcript(self, records, codex=False):
        relative = (".codex/sessions/2026/11/10/rollout-test-sid-W.jsonl" if codex else
                    ".claude/projects/-test/sid-W.jsonl")
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in records) + "\n")
        return path

    def test_relay_requires_question_coverage_not_only_claim_checks(self):
        self.finish()
        prompt = self.spawns[0][0][-1]
        self.assertIn("original question as asked", prompt)
        self.assertIn("recommendation and next action", prompt)
        self.assertNotIn("주장 밖의 새 문제를 찾거나", prompt)

    def test_checker_gets_source_transcript_location_for_both_harnesses(self):
        for codex in (False, True):
            with self.subTest(codex=codex):
                path = self.transcript([], codex=codex)
                self.finish(DONE + str(codex))
                prompt = self.spawns[-1][0][-1]
                self.assertIn(str(path), prompt)
                self.assertIn("source user requests", prompt)
                path.unlink()

    def test_claude_recording_restrictions_follow_the_check_without_copying_subject(self):
        self.transcript([{"type": "user", "message": {"content":
            "이 임시 조사 내용은 깃헙 이슈나 워크로그 같은 건 일절 남기지말아줘. 테스트는 실행하세요."}}])
        self.finish()
        prompt = self.spawns[0][0][-1]
        self.assertIn("원 작업의 기록 금지 제한", prompt)
        self.assertIn("work-log", prompt)
        self.assertIn("GitHub issue", prompt)
        self.assertNotIn("임시 조사", prompt, "do not replicate the restricted subject")
        self.assertNotIn("테스트는 실행하세요", prompt, "copy restrictions, not source tasks")

    def test_codex_restrictions_are_read_again_for_a_follow_up(self):
        path = self.transcript([{"type": "response_item", "payload": {"type": "message", "role": "user",
            "content": [{"type": "input_text", "text": "Do not save this analysis in memory."}]}}], codex=True)
        self.finish()
        self.assertIn("memory", self.spawns[-1][0][-1])
        with path.open("a") as stream:
            stream.write(json.dumps({"type": "response_item", "payload": {"type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "워크로그에도 남기지 마세요."}]}}, ensure_ascii=False) + "\n")
        self.finish(DONE.replace("12개", "13개"))
        self.assertIn("memory", self.spawns[-1][0][-1])
        self.assertIn("work-log", self.spawns[-1][0][-1])

    def test_evidence_and_injected_instructions_are_not_recording_restrictions(self):
        self.transcript([
            {"type": "assistant", "message": {"content": "메모리에 기록하지 마세요."}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "content": "워크로그에 남기지 마세요."}]}},
            {"type": "user", "message": {"content": '# AGENTS.md\n워크로그에 남기지 마세요.'}},
            {"type": "user", "message": {"content": '> 워크로그에 남기지 마세요.\n```text\n이슈에 남기지 마세요.\n```\n인용: “메모리에 기록하지 마세요.”'}},
            {"type": "user", "origin": {"kind": "hook"}, "message": {"content": "워크로그에 남기지 마세요."}},
        ])
        self.finish()
        self.assertNotIn("원 작업의 기록 금지 제한", self.spawns[0][0][-1])

    def test_codex_harness_text_does_not_supply_recording_restrictions(self):
        self.transcript([{"type": "response_item", "payload": {"type": "message", "role": "user",
            "content": [{"type": "input_text", "text": "Do not save anything in memory."}],
            "internal_chat_message_metadata_passthrough": {"content_item_metadata": [
                {"provenance": {"type": "harness"}}]}}}], codex=True)
        self.finish()
        self.assertNotIn("원 작업의 기록 금지 제한", self.spawns[0][0][-1])

    def test_describing_a_previous_restriction_does_not_create_a_new_one(self):
        self.transcript([{"type": "user", "message": {"content":
            "work-log에서 전에 남기지 말라고 한 항목 하나를 지우세요."}}])
        self.finish()
        self.assertNotIn("원 작업의 기록 금지 제한", self.spawns[0][0][-1])

    def test_recording_ban_title_does_not_turn_past_instruction_data_into_a_ban(self):
        body = ("오늘 work-log에서 전에 남기지 말라고 한 항목 하나를 지웁니다. "
                "그리고 자동 확인 세션에도 제한을 전달합니다.")
        for title in ("1) 남기지 말 것: ", "## 남기지 말 것: "):
            with self.subTest(title=title):
                self.transcript([{"type": "user", "message": {"content": title + body}}])
                self.finish(DONE + title)
                self.assertNotIn("원 작업의 기록 금지 제한", self.spawns[-1][0][-1])

    def test_direct_destination_with_no_recording_infinitive_still_applies(self):
        self.transcript([{"type": "user", "message": {"content": "work-log에 남기지 말 것"}}])
        self.finish()
        self.assertIn("work-log", self.spawns[0][0][-1])
        self.assertIn("원 작업의 기록 금지 제한", self.spawns[0][0][-1])

    def test_no_recording_label_with_only_destinations_is_an_explicit_restriction(self):
        examples = (("남기지 말 것: work-log, GitHub issue", ("work-log", "GitHub issue")),
                    ("1) 남기지 말 것: work-log와 메모리", ("work-log", "memory")))
        for text, targets in examples:
            with self.subTest(text=text):
                self.transcript([{"type": "user", "message": {"content": text}}])
                self.finish(DONE + text)
                prompt = self.spawns[-1][0][-1]
                self.assertIn("원 작업의 기록 금지 제한", prompt)
                for target in targets:
                    self.assertIn(target, prompt)

    def test_malformed_source_records_do_not_prevent_valid_restrictions_or_delivery(self):
        path = self.transcript([None, {"type": "user", "message": "bad"},
            {"type": "response_item", "payload": "bad"},
            {"type": "user", "message": {"content": "워크로그에 남기지 마세요."}}])
        with path.open("a") as stream:
            stream.write("{unfinished\n")
        self.finish()
        self.assertIn("work-log", self.spawns[0][0][-1])

    def test_queued_claude_restriction_keeps_an_inline_destination(self):
        self.transcript([{"type": "attachment", "attachment": {"type": "queued_command",
            "origin": {"kind": "human"}, "prompt": "`work-log`에 남기지 마세요."}}])
        self.finish()
        self.assertIn("work-log", self.spawns[0][0][-1])

    def test_unspecified_recording_ban_stays_unspecified(self):
        self.transcript([{"type": "user", "message": {"content": "이 분석 결과는 남기지 마세요."}}])
        self.finish()
        prompt = self.spawns[0][0][-1]
        self.assertIn("별도 기록", prompt)
        self.assertNotIn("분석 결과", prompt)

    def test_result_opens_check_then_delivers_combined_report_once(self):
        self.finish()
        self.assertNotIn("w1:p2", self.closed, "keep the original worker available for follow-up")
        self.assertEqual(len(self.tells), 0, "the work report must not wake the main pane")
        self.assertEqual(len(self.spawns), 1)
        args, env = self.spawns[0]
        self.assertIn("--check", args)
        self.assertEqual(args[args.index("--kind") + 1], "auto")
        self.assertEqual(args[args.index("--cwd") + 1], "/repo with spaces")
        self.assertEqual(env["HERDR_PANE_ID"], "w5:p3")
        self.assertEqual(env["HERDR_SOCKET_PATH"], "/default/herdr.sock")
        self.assertIn("항목", args[-1])
        self.assertIn("항목마다", args[-1])
        self.assertIn(str(self.root / "reports"), args[-1])
        self.finish(CHECKED, check=True)
        self.assertEqual(len(self.spawns), 1, "a check never gets another check")
        self.assertEqual(len(self.tells), 1)
        address, message = self.tells[0]
        self.assertEqual(address, "default/w5:p3")
        for item in ("Job", DONE.splitlines()[0], CHECKED.splitlines()[0], "전체 보고:", "항목"):
            self.assertIn(item, message)
        self.assertNotIn("세부 근거는 보존됩니다", message)
        self.assertIn("일 칸: workers/w1:p2", message)
        self.assertIn("w1:p9", self.closed, "independent checks still close after their report")
        self.finish(CHECKED, check=True)
        self.finish()
        self.assertEqual(len(self.tells), 1, "duplicate Stop/sweep must not redeliver")
        self.assertEqual(len(self.spawns), 1)

    def test_follow_up_uses_same_worker_and_gets_a_fresh_independent_check(self):
        self.finish()
        self.finish(CHECKED, check=True)
        self.finish(DONE.replace("12개", "13개"))
        self.assertNotIn("w1:p2", self.closed)
        self.assertEqual(len(self.spawns), 2)
        self.assertTrue(all("--check" in args for args, env in self.spawns))

    def test_combined_report_resolves_a_moved_worker_by_terminal(self):
        self.finish()
        self.worker["pane_id"] = "w2:p7"
        self.finish(CHECKED, check=True)
        self.assertIn("일 칸: workers/w2:p7", self.tells[-1][1])

    def test_yoyoo_decision_is_delivered_immediately(self):
        self.finish("배포 승인이 필요합니다.\n남은 것: 요유 몫: prod 허용 입력 · 유요 몫: 대기 · 세션: 닫으면 승인 대기 멈춤")
        self.assertEqual(len(self.spawns), 0)
        self.assertEqual(len(self.tells), 1)
        self.assertIn("prod 허용", self.tells[0][1])
        self.assertNotIn(auto.share.DELEGATE_NOTE, self.tells[0][1])

    def test_question_with_no_yoyoo_field_is_direct(self):
        self.finish("질문: staging 결과를 먼저 볼까요?\n남은 것: 유요 몫: 답 대기 · 세션: 닫아도 됨")
        self.assertEqual(len(self.spawns), 0)
        self.assertEqual(len(self.tells), 1)
        self.assertNotIn(auto.share.DELEGATE_NOTE, self.tells[0][1])

    def test_no_result_is_delivered_immediately(self):
        self.finish("확인할 결과가 없습니다. 답을 기다립니다.\n남은 것: 요유 몫: 없음 · 세션: 닫아도 됨")
        self.assertEqual(len(self.spawns), 0)
        self.assertEqual(len(self.tells), 1)

    def test_completed_question_handler_and_url_are_still_checked(self):
        self.finish(DONE.replace("pytest", "질문 처리 테스트") + "\n참고: https://example.test/?q=test")
        self.assertEqual(len(self.spawns), 1)
        self.assertEqual(len(self.tells), 0)

    def test_spawn_failure_preserves_and_delivers_original(self):
        self.spawn_error = "workers unavailable"
        self.finish()
        self.assertEqual(len(self.spawns), 1)
        self.assertEqual(len(self.tells), 1)
        self.assertIn("workers unavailable", self.tells[0][1])
        self.assertIn(DONE.splitlines()[0], self.tells[0][1])
        self.assertTrue(any(DONE in f.read_text() for f in (self.root / "reports").glob("*.md")))

    def test_spawn_exception_also_delivers_original(self):
        self.spawn_error = OSError("no executable")
        self.finish()
        self.assertEqual(len(self.tells), 1)
        self.assertIn("no executable", self.tells[0][1])

    def test_spawn_timeout_also_delivers_original(self):
        self.spawn_error = subprocess.TimeoutExpired("pane-spawn", 300)
        self.finish()
        self.assertEqual(len(self.tells), 1)
        self.assertIn("timed out", self.tells[0][1])

    def test_failed_delivery_retries_without_losing_or_rechecking_report(self):
        self.finish()
        self.tell_exit = 1
        self.finish(CHECKED, check=True)
        self.assertNotIn("w1:p9", self.closed)
        self.assertEqual(len(self.alerts), 1, "failed delivery alerts outside pane-tell")
        title, body = self.alerts[0]
        self.assertEqual(title, "세션 보고를 전달하지 못했습니다")
        self.assertIn("default/w5:p3", body)
        self.assertIn(str(self.root / "reports"), body)
        for line in CHECKED.splitlines():
            self.assertNotIn(line, body, "alerts contain metadata, not report text")
        failures = list((self.root / "state/pane-tell/failed-reports").glob("report.*"))
        self.assertEqual(len(failures), 1)
        metadata = json.loads(failures[0].read_text())
        self.assertEqual(metadata["receiver"], "default/w5:p3")
        self.assertTrue(Path(metadata["report"]).is_file())
        self.assertIn(metadata["report"], body)
        self.assertIn(CHECKED, Path(metadata["report"]).read_text())
        self.tell_exit = 0
        self.finish(CHECKED, check=True)
        self.assertIn("w1:p9", self.closed)
        self.assertEqual(len(self.spawns), 1)
        self.assertEqual(len(self.tells), 2, "only the successful delivery is marked as delivered")
        self.assertEqual(len(self.alerts), 1, "successful retry does not send another failure alert")

    def test_slack_not_posted_report_selects_auto(self):
        for word in ("Slack", "슬랙"):
            with self.subTest(word=word):
                self.finish(DONE + f"\n{word}에는 게시하지 않았습니다")
                self.assertTrue(self.spawns, "a result must open a check")
                args = self.spawns[-1][0]
                self.assertEqual(args[args.index("--kind") + 1], "auto")

    def test_network_or_process_evidence_selects_auto(self):
        # 요유 2026-11-07: a Codex check pane has the network, gcloud and process reads, so no word sends it to Claude.
        for word in ("gcloud", "bq", "pgrep", "curl", "launchctl", "소켓", "네트워크"):
            with self.subTest(word=word):
                self.finish(DONE.replace("pytest", word) + f"\n{word}을 실행해 확인했습니다.")
                self.assertTrue(self.spawns, "a result must open a check")
                args = self.spawns[-1][0]
                self.assertEqual(args[args.index("--kind") + 1], "auto")

    def test_other_lead_and_home_are_unchanged(self):
        for terminal, name in (("term_L", "spawn-L-1"), ("term_M", "spawn-M-h1")):
            with self.subTest(name=name):
                self.names["w1:p2"] = name
                self.main["terminal_id"] = terminal
                self.finish()
                self.assertEqual(len(self.spawns), 0)
                self.assertIn(auto.share.RELAY_NOTE if terminal == "term_L" else auto.share.DELEGATE_NOTE,
                              self.tells[-1][1])

    def test_check_failure_is_delivered_without_another_check(self):
        self.finish()
        self.finish(CHECKED.replace("PASS", "FAIL: 1 failed"), check=True)
        self.assertEqual(len(self.spawns), 1)
        self.assertEqual(len(self.tells), 1)
        self.assertIn("FAIL: 1 failed", self.tells[0][1])

    def test_checker_question_does_not_consume_final_result(self):
        self.finish()
        question = "질문: 어느 명령을 실행할까요?\n남은 것: 요유 몫: 명령 선택 · 세션: 닫으면 답 대기 멈춤"
        self.finish(question, check=True)
        self.finish(question, check=True)
        self.assertEqual(len(self.tells), 1, "duplicate questions are not redelivered")
        self.assertTrue(self.tells[0][1].startswith("바로 전달하는"))
        self.finish(CHECKED, check=True)
        self.assertEqual(len(self.tells), 2)
        self.assertIn("PASS", self.tells[-1][1])

    def test_direct_report_does_not_trigger_missing_checker_guard(self):
        self.finish("질문: 어느 안을 고를까요?\n남은 것: 요유 몫: A 또는 B 결정 · 세션: 닫아도 됨")
        transcript = self.root / "main.jsonl"
        transcript.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": self.tells[0][1]}},
                                         ensure_ascii=False) + "\n")
        report = "모아 두었습니다.\n남은 것: 요유 몫: A 또는 B 결정 · 유요 몫: 없음 · 세션: 닫아도 됨"
        with patch.object(auto.share, "delegates_checks", return_value=True):
            self.assertIsNone(auto.share.relay_reason({"transcript_path": str(transcript)}, report, self.env))


if __name__ == "__main__":
    unittest.main()
