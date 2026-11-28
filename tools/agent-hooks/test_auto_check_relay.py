#!/usr/bin/env python3
"""Routing contracts: run finish(), replace only external herdr/process boundaries. Pre-push: --selftest."""
import importlib.util
import json
import os
import re
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REAL_RUN, REAL_POPEN = subprocess.run, subprocess.Popen

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
        self.checkers = []
        self.names = {"w1:p2": "spawn-M-1"}
        self.tells, self.spawns, self.closed, self.alerts, self.prechecks = [], [], [], [], []
        self.wake_queries = []
        self.spawn_error = None
        self.spawn_runner = None  # (runner, reason) pane-spawn's receipt reports; None leaves the receipt empty
        self.tell_exit = 0
        self.tell_out, self.on_tell = "told default/w5:p3 (👑 test receiver)\n", None
        self.tell_envs, self.finalizers = [], []
        self.precheck_exit = 0
        self.env = dict(HOME=str(self.root), XDG_STATE_HOME=str(self.root / "state"),
                        HERDR_PANE_ID="w1:p2", HERDR_SOCKET_PATH="/sessions/workers/herdr.sock",
                        PANE_REPORT_DIR=str(self.root / "reports"), AUTO_TAB_CLOSE_PANE_SPAWN="/fake/pane-spawn",
                        AUTO_TAB_CLOSE_PANE_TELL="/fake/pane-tell", AUTO_TAB_CLOSE_PANE_LABEL="/fake/pane-label",
                        HERDR_ERROR_NOTIFY="/fake/herdr-error-notify", HERDR_BIN_PATH="/fake/herdr")
        for target, value in (("herdr", self.herdr), ("subprocess.run", self.call_process), ("subprocess.Popen", self.popen),
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

    def popen(self, args, **kwargs):
        # Startup now uses Popen to cancel an entire failed launcher process
        # group. Keep the real retry/receipt code; replace only process I/O.
        failure = None
        try:
            result = self.call_process(args, **kwargs)
        except subprocess.TimeoutExpired as exc:
            failure = exc
            result = subprocess.CompletedProcess(args, 124, "", "")

        class Process:
            pid = 999999999
            returncode = result.returncode

            def communicate(self, timeout=None):
                nonlocal failure
                if failure:
                    exc, failure = failure, None
                    raise exc
                return result.stdout or "", result.stderr or ""

        return Process()

    def all_panes(self, env):
        return {"term_M": ("default/w5:p3", self.main), "term_W": ("w1:p2", self.worker)}

    def herdr(self, env, *args, **kwargs):
        panes = [self.worker, *self.checkers]
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
        if args == ["/fake/herdr", "pane", "list"]:
            self.wake_queries.append((args, kwargs["env"]))
            # The registered main has no active runtime in this fixture. The
            # real wake must stop here and leave its durable alert unconsumed.
            return subprocess.CompletedProcess(args, 0, json.dumps({"result": {"panes": [self.main]}}), "")
        if args[0].endswith("pane-spawn"):
            self.spawns.append((args, kwargs["env"]))
            if self.spawn_error:
                if isinstance(self.spawn_error, Exception):
                    raise self.spawn_error
                return subprocess.CompletedProcess(args, 1, "", self.spawn_error)
            if self.spawn_runner and kwargs["env"].get("PANE_SPAWN_CHECK_RECEIPT"):
                Path(kwargs["env"]["PANE_SPAWN_CHECK_RECEIPT"]).write_text(json.dumps(
                    {"status": "started", "runner": self.spawn_runner[0], "runner_reason": self.spawn_runner[1]}))
            number = 9 + len(self.checkers)
            self.checker = dict(self.worker, pane_id=f"w1:p{number}", terminal_id=f"term_C{number}",
                                label="🟢 🫡 확인 " + args[-2], agent_session={"value": f"sid-C{number}"})
            self.checkers.append(self.checker)
            self.names[f"w1:p{number}"] = f"spawn-M-chk{number}"
            return subprocess.CompletedProcess(args, 0, f"workers/w1:p{number}\n", "")
        if "--redeliver-check" in args:
            self.finalizers.append((args, kwargs))
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[0].endswith("pane-tell"):
            # The pre-check report has its own contract below; every other count here pins the routes it leaves unchanged.
            if args[2].startswith("확인 전 보고입니다("):
                self.prechecks.append((args[1], args[2], kwargs["env"].get("PANE_TELL_BATCH")))
                return subprocess.CompletedProcess(args, self.precheck_exit)
            self.tells.append((args[1], args[2]))
            self.tell_envs.append(kwargs["env"])
            if self.on_tell:
                self.on_tell(args[2])
            return subprocess.CompletedProcess(args, self.tell_exit, self.tell_out, "")
        if args[0].endswith("pane-label"):
            self.closed.append(kwargs["env"]["HERDR_PANE_ID"])
            return subprocess.CompletedProcess(args, 0)
        if args[0] == "/fake/herdr-error-notify":
            self.alerts.append(args[1:])
            return subprocess.CompletedProcess(args, 0)
        raise AssertionError(args)

    def finish(self, text=DONE, check=False, check_id=None):
        auto.finish(text, {**self.env, "HERDR_PANE_ID": check_id or ("w1:p9" if check else "w1:p2")})

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

    def test_dash_prefixed_job_is_after_option_delimiter(self):
        for name in ('--help', '--unknown', '-h', '--'):
            with self.subTest(name=name):
                self.worker['label'] = '🟢 🫡 ' + name
                self.finish(name + '\n' + DONE)
                args = self.spawns[-1][0]
                job = args[-2]
                self.assertTrue(job.startswith(name + ' [ac-'), job)
                self.assertEqual(args[-3], '--')

    def test_dash_prefixed_job_reaches_real_launcher(self):
        self.worker['label'] = '🟢 🫡 --help'
        self.finish()
        args = self.spawns[0][0]
        fixture = Path(__file__).with_name('pane-spawn-test-fixture.sh')
        # Keep the real parser and startup flow, replacing only the herdr CLI.
        with patch.object(subprocess, 'Popen', REAL_POPEN):
            result = REAL_RUN(['bash', '-c', 'source "$0"; workers_up; is_main; ps "$@"',
                               str(fixture), *args[1:]], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'workers/w1:new')

    def test_checker_gets_source_transcript_location_for_both_harnesses(self):
        for codex in (False, True):
            with self.subTest(codex=codex):
                path = self.transcript([], codex=codex)
                self.finish(DONE + str(codex))
                prompt = self.spawns[-1][0][-1]
                self.assertIn(str(path), prompt)
                self.assertIn("source user requests", prompt)
                path.unlink()

    def test_missing_request_file_uses_isolated_safe_reader_for_both_harnesses(self):
        # Exercise the emitted command against the real reader, including shell quoting.
        helper = self.root / '.claude/hooks/session-user-messages.py'
        helper.parent.mkdir(parents=True)
        helper.symlink_to(Path(__file__).parents[1] / 'claude/hooks/session-user-messages.py')
        for codex in (False, True):
            with self.subTest(codex=codex):
                text = 'Check the change. password=private-fixture-value'
                user = ({'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': text}}
                        if codex else {'type': 'user', 'message': {'content': text}})
                path = self.transcript([user, {'type': 'assistant', 'message': {'content': 'PRIVATE_TOOL_FIXTURE'}}], codex)
                quoted_path = path.with_name("request 'with spaces'-sid-W.jsonl")
                path.rename(quoted_path)
                with patch.object(auto, 'source_transcripts', return_value=[str(quoted_path)]), \
                        patch.object(auto, 'quote_requests', return_value=''):
                    self.finish(DONE + str(codex))
                prompt = self.spawns[-1][0][-1]
                commands = prompt.split('Safe user-request commands (run each line):\n', 1)[1].split('\nEnd safe user-request commands.', 1)[0]
                args = shlex.split(commands)
                self.assertEqual(args, ['uv', 'run', '--no-project', 'python', '-I', str(helper), str(quoted_path)])
                self.assertIn('Do not parse or print the raw transcript', prompt)
                self.assertIn('If the reader fails or redacts required scope, report coverage as unverified', prompt)
                self.assertNotIn('and the transcript only for what it cuts', prompt)
                with patch.object(subprocess, 'Popen', REAL_POPEN):
                    result = REAL_RUN(args, env={**os.environ, 'HOME': str(self.root)}, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('Check the change.', result.stdout)
                self.assertNotIn('private-fixture-value', result.stdout)
                self.assertNotIn('PRIVATE_TOOL_FIXTURE', result.stdout)
                quoted_path.unlink()

    def test_request_file_write_failure_keeps_safe_fallback(self):
        self.transcript([{'type': 'user', 'message': {'content': 'Check the original request.'}}])
        original = os.open

        def open_file(path, *args, **kwargs):
            if str(path).endswith('.requests.txt'):
                raise OSError('fixture request-file write failure')
            return original(path, *args, **kwargs)

        with patch.object(os, 'open', side_effect=open_file):
            self.finish()
        prompt = self.spawns[-1][0][-1]
        self.assertNotIn('The source user requests are collected in', prompt)
        self.assertIn('Safe user-request commands (run each line):', prompt)
        self.assertIn('Do not parse or print the raw transcript', prompt)

    def test_emitted_reader_passes_guard_while_raw_transcript_print_is_blocked(self):
        spec = importlib.util.spec_from_file_location('secret_guard',
            Path(__file__).parents[1] / 'claude/hooks/secret-print-guard.py')
        guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(guard)
        for relative in ('.codex/sessions/2026/01/01/fixture.jsonl', '.claude/projects/-fixture/fixture.jsonl'):
            with self.subTest(relative=relative):
                path = str(Path.home() / relative)
                with patch.dict(self.env, HOME=str(Path.home())), \
                        patch.object(auto, 'source_transcripts', return_value=[path]), \
                        patch.object(auto, 'quote_requests', return_value=''):
                    self.finish(DONE + relative)
                prompt = self.spawns[-1][0][-1]
                command = prompt.split('Safe user-request commands (run each line):\n', 1)[1].split('\nEnd safe user-request commands.', 1)[0]
                problem = lambda cmd: guard.tool_problem({'tool_name': 'Bash', 'tool_input': {'command': cmd}})
                self.assertIsNone(problem(command))
                self.assertTrue(problem(shlex.join(['cat', path])))
                raw = 'from pathlib import Path; print(Path(' + repr(path) + ').read_text())'
                self.assertTrue(problem("uv run --no-project python - <<'PY'\n" + raw + '\nPY'))

    def test_report_format_directives_do_not_disable_request_collection(self):
        for codex in (False, True):
            for style in ('형식(설명 페이지, `what`/`why`)은', '형식은', '양식을', '서식은', '말투는', '표현을'):
                with self.subTest(codex=codex, style=style):
                    text = f'보고는 리드가 읽으니 이 {style} 쓰지 말고, 확인할 결과와 근거를 적으세요.'
                    record = lambda body: ({'type': 'response_item', 'payload': {
                        'type': 'message', 'role': 'user', 'content': body}} if codex else
                        {'type': 'user', 'message': {'content': body}})
                    path = self.transcript([record(text)], codex)
                    self.finish(DONE + style + str(codex))
                    prompt = self.spawns[-1][0][-1]
                    self.assertNotIn('원 작업의 기록 금지 제한', prompt)
                    self.assertIn(text, self.requests_file(prompt).read_text())
                    # The same open session receives a follow-up: collect both requests again.
                    with path.open('a') as stream:
                        stream.write(json.dumps(record('Include the follow-up request.')) + '\n')
                    self.finish(DONE + style + str(codex) + ' follow-up')
                    quoted = self.requests_file(self.spawns[-1][0][-1]).read_text()
                    self.assertIn(text, quoted)
                    self.assertIn('Include the follow-up request.', quoted)
                    path.unlink()

    def test_report_format_exception_preserves_real_storage_bans(self):
        for text in ('이 형식은 쓰지 말고, 분석 결과는 남기지 마세요.',
                     '이 형식은 쓰지 말고, 결과를 work-log에 저장하지 마세요.',
                     '이 형식으로 조사 결과를 기록하지 마세요.',
                     '분석 결과를 쓰지 마세요.'):
            with self.subTest(text=text):
                self.transcript([{'type': 'user', 'message': {'content': text}}])
                self.finish(DONE + text)
                prompt = self.spawns[-1][0][-1]
                self.assertIn('원 작업의 기록 금지 제한', prompt)
                self.assertNotIn('The source user requests are collected in', prompt)
                self.assertIn('Safe user-request commands (run each line):', prompt)

    def test_checker_prompt_batches_commands_and_quotes_source_requests(self):
        # 11/16, 127 checks: time rose with tool calls (r=0.63), and ~16% went to finding the source request.
        self.transcript([
            {"type": "user", "message": {"content": "# AGENTS.md\nscaffold"}},
            {"type": "user", "message": {"content": "api#1 로그인 오류를 고치고 테스트를 남기세요."}},
            {"type": "assistant", "message": {"content": "알겠습니다."}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "content": "tool output"}]}},
            {"type": "user", "message": {"content": [{"type": "text", "text": "추가: PR 링크도 적으세요."}]}},
            {"type": "user", "isMeta": True, "message": {"content": "Base directory for this skill: skill body"}},
            {"type": "user", "isCompactSummary": True, "message": {"content": "This session is being continued: summary"}},
        ])
        self.finish()
        prompt = self.spawns[0][0][-1]
        self.assertIn("한 번의 명령", prompt)
        requests = self.requests_file(prompt)
        self.assertEqual(requests.stat().st_mode & 0o777, 0o600)
        quoted = requests.read_text()
        self.assertIn("api#1 로그인 오류를 고치고 테스트를 남기세요.", quoted)
        self.assertIn("추가: PR 링크도 적으세요.", quoted)
        self.assertLess(quoted.index("api#1"), quoted.index("추가: PR"), "in the order they came")
        self.assertNotIn("api#1 로그인", prompt, "a prompt is a process argument: requests stay in the file")
        for absent in ("scaffold", "알겠습니다", "tool output", "skill body", "summary"):
            self.assertNotIn(absent, quoted)

    def requests_file(self, prompt):
        path = Path(json.loads(prompt.split("The source user requests are collected in ", 1)[1].split(": read it first", 1)[0]))
        self.assertEqual(path.parent, self.state / "automatic-checks")
        return path

    def test_long_source_requests_keep_the_assignment_and_latest_follow_up(self):
        half = auto.REQUEST_CAP // 2
        path = self.transcript([{"type": "user", "message": {"content": text * auto.REQUEST_CAP}} for text in "XYZ"])
        self.finish()
        prompt = self.spawns[0][0][-1]
        quoted = self.requests_file(prompt).read_text()
        self.assertIn("X" * half, quoted)
        self.assertIn("Z" * half, quoted)
        self.assertNotIn("Y" * 100, quoted)
        self.assertNotIn("X" * (half + 1), quoted)
        self.assertIn("가운데는 전용 요청 읽기 명령으로", quoted)
        self.assertIn('Safe user-request commands (run each line):', prompt)
        self.assertIn('Do not parse or print the raw transcript', prompt)
        self.assertIn(str(path), prompt)

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
        self.assertEqual(list((self.state / "automatic-checks").glob("*.requests.txt")), [], "nor copy the requests to a file")

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
        self.assertEqual(args[args.index("--kind") + 1], "codex")
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
        self.assertEqual(message.count(DONE.splitlines()[-1]), 2)
        self.assertIn(f"전체 보고: {self.root / 'reports'}", message)
        self.assertIn(f"확인 전체 보고: {self.root / 'reports'}", message)
        self.assertIn("일 칸: workers/w1:p2", message)
        self.assertIn("확인 종류: codex (", message)
        self.assertTrue(any(DONE in f.read_text() for f in (self.root / "reports").glob("*.md")))
        self.assertIn("w1:p9", self.closed, "independent checks still close after their report")
        self.finish(CHECKED, check=True)
        self.finish()
        self.assertEqual(len(self.tells), 1, "duplicate Stop/sweep must not redeliver")
        self.assertEqual(len(self.spawns), 1)

    def test_combined_report_names_the_runner_pane_spawn_opened_not_the_classifier_guess(self):
        # Persisted legacy receipts remain authoritative; new check requests always use Codex.
        for runner, reason in (("claude", "claude first: within reserve"), ("codex", "claude first: Claude past reserve; Codex within reserve")):
            with self.subTest(runner=runner):
                self.setUp()
                self.spawn_runner = (runner, reason)
                self.finish()
                self.finish(CHECKED, check=True)
                self.assertIn(f"확인 종류: {runner} ({reason})", self.tells[-1][1])
                self.assertNotIn("확인 종류: auto (", self.tells[-1][1])
                (context_path,) = [p for p in (self.state / "automatic-checks").glob("*.json") if p.name.count(".") == 1]
                context = json.loads(context_path.read_text())
                self.assertEqual((context["runner"], context["runner_reason"]), (runner, reason))
                self.assertEqual(context["kind"], "codex", "new checks use their own runtime policy")

    def test_check_start_sends_main_a_precheck_report_that_is_not_check_evidence(self):
        self.finish()
        self.assertEqual(len(self.prechecks), 1, "main hears the result when its check opens, so it can answer early")
        address, message, batch = self.prechecks[0]
        self.assertEqual(address, "default/w5:p3")
        self.assertEqual(batch, "0", "the early answer must not wait in the idle relay batch")
        (context_path,) = [p for p in (self.state / "automatic-checks").glob("*.json") if p.name.count(".") == 1]
        for item in (DONE.splitlines()[0], f"[ac-{context_path.stem}]", "일 칸: workers/w1:p2",
                     "확인 칸: workers/w1:p9", f"전체 보고: {self.root / 'reports'}"):
            self.assertIn(item, message)
        self.assertNotIn("세부 근거는 보존됩니다", message, "short form; the whole reply stays in the file")
        self.assertNotIn(auto.share.CHECK_NOTE, message)
        self.assertIn("요유 이름의 초안(--yoyoo-draft), reply, 팀 전달, decide", message, "#180 review: no early draft or team post")
        self.assertNotIn("check_delivered", json.loads(context_path.read_text()),
                         "main-channel.py accepts an [ac-ID] only after check_delivered")
        # share-guard: a 확인: line resting on this report is refused; no second check is demanded either.
        claim = "확인: 확인 칸이 테스트 통과를 봤습니다.\n남은 것: 요유 몫: 없음 · 세션: 닫아도 됨"
        self.assertEqual(self.main_blocks(message, claim), auto.share.PRECHECK_REASON)
        own = "확인: 테스트 통과를 봤습니다.\n남은 것: 요유 몫: 없음 · 세션: 닫아도 됨"  # Codex review: no pane named
        self.assertEqual(self.main_blocks(message, own), auto.share.PRECHECK_REASON)
        self.assertIsNone(self.main_blocks(message, "확인: 없음\n남은 것: 요유 몫: 없음 · 세션: 열어 둠"))
        self.assertIsNone(self.main_blocks(message, "먼저 올릴 답을 '작업 중(확인 중):'으로 올렸습니다.\n남은 것: 요유 몫: 없음 · 세션: 열어 둠"))
        self.finish()
        self.assertEqual(len(self.prechecks), 1, "a replayed Stop does not resend it")
        self.finish(CHECKED, check=True)
        self.assertEqual(len(self.tells), 1, "the combined report still comes separately")
        self.assertTrue(self.tells[0][1].startswith("확인 칸의 마지막 보고입니다("))
        self.assertIsNone(self.main_blocks(self.tells[0][1], claim), "after the check, the same line is accepted")
        self.assertEqual(len(self.prechecks), 1)

    def test_precheck_report_keeps_an_unanswered_check_report_rule(self):
        # #69 review: PANE_TELL_BATCH=0 puts a 확인 전 보고 into a running turn (queued_command) or the next prompt while another
        # job's combined report still waits for its last report. That report's rule decides: its 확인 칸 line passes, none is refused.
        self.finish()
        precheck = self.prechecks[0][1]
        self.finish(CHECKED, check=True)
        combined = self.tells[0][1]
        claim = "확인: 확인 칸이 테스트 통과를 봤습니다.\n남은 것: 요유 몫: 없음 · 세션: 닫아도 됨"
        bare = "모아 두었습니다.\n남은 것: 요유 몫: 없음 · 유요 몫: 없음 · 세션: 닫아도 됨"
        user = lambda text: {"type": "user", "message": {"role": "user", "content": text}}
        say = lambda text: {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}
        queued = {"type": "attachment", "attachment": {"type": "queued_command", "prompt": precheck}}
        for late in (queued, user(precheck)):
            with self.subTest(late=late["type"]):
                entries = [user(combined), say("확인 칸 보고를 읽습니다."), late]
                self.assertIsNone(self.main_blocks(None, claim, entries))
                self.assertEqual(self.main_blocks(None, bare, entries), auto.share.CHECK_REPORT_REASON)
        # A last report already answered the combined report: the 확인 전 보고 alone decides again.
        entries = [user(combined), say(claim), user(precheck)]
        self.assertEqual(self.main_blocks(None, claim, entries), auto.share.PRECHECK_REASON)
        self.assertIsNone(self.main_blocks(None, bare, entries))

    def test_lost_precheck_report_is_recorded_and_alerted_while_the_combined_report_still_comes(self):
        # #69 review: a lost 확인 전 보고 left no trace of why the early answer never came.
        self.precheck_exit = 1
        self.finish()
        self.assertEqual(len(self.prechecks), 1)
        (context_path,) = [p for p in (self.state / "automatic-checks").glob("*.json") if p.name.count(".") == 1]
        self.assertEqual(json.loads(context_path.read_text())["precheck_error"], "pane-tell failed")
        failures = list((self.root / "state/pane-tell/failed-reports").glob("report.*"))
        self.assertEqual(len(failures), 1)
        self.assertEqual(json.loads(failures[0].read_text())["receiver"], "default/w5:p3")
        self.assertEqual(len(self.alerts), 1)
        self.finish()
        self.assertEqual(len(self.prechecks), 1, "a replayed Stop does not resend it")
        self.finish(CHECKED, check=True)
        self.assertEqual(len(self.tells), 1, "the combined report still comes")
        self.assertTrue(self.tells[0][1].startswith("확인 칸의 마지막 보고입니다("))

    def test_no_precheck_report_when_the_check_could_not_open(self):
        self.spawn_error = "simulated check start failure"
        self.finish()
        self.assertEqual(self.prechecks, [], "the failed-start fallback already delivers the original report")
        self.assertEqual(len(self.tells), 1)

    def test_completed_check_delivers_receipt_without_requesting_pane_close(self):
        self.completed_check_replay(codex=False)

    def test_codex_completed_check_delivers_once_in_new_and_open_sessions(self):
        self.completed_check_replay(codex=True)

    def completed_check_replay(self, codex):
        self.finish()
        checked = ("확인했습니다.\nTASK_CHECK: pass\n"
                   "남은 것: 요유 몫: 없음 · 유요 몫: 리드가 PR 교차 검토 · 세션: 확인 완료·실행 중 작업 없음")
        transcript = self.transcript([{"type": "assistant", "message": {
            "content": [{"type": "text", "text": checked}]}}])
        payload = {"last_assistant_message": checked} if codex else {"transcript_path": str(transcript)}
        # First report from a new checker, then the same Stop in its still-open session.
        for _ in range(2):
            text = auto.decide(payload, {**self.env, "HERDR_PANE_ID": "w1:p9"})
            self.assertEqual(text, checked)
            self.finish(text, check=True)
        context = json.loads(next((self.state / "automatic-checks").glob("*.json")).read_text())
        self.assertIs(context.get("check_delivered"), True)
        self.assertEqual(len(self.tells), 1)
        self.assertIn("확인 칸의 마지막 보고", self.tells[0][1])
        self.assertNotIn("w1:p9", self.closed, "delivery does not grant permission to close a pane")
        self.assertEqual(len(self.spawns), 1, "a finished check never requests another check")

    def test_completion_alias_requires_one_real_terminal_marker_and_no_running_work(self):
        completed = "확인했습니다.\nTASK_CHECK: pass\n남은 것: 요유 몫: 없음 · 세션: 확인 완료·실행 중 작업 없음"
        for result in ("pass", "fail", "unknown"):
            self.assertTrue(auto.check_finished(completed.replace("TASK_CHECK: pass", "TASK_CHECK: " + result)))
        for report in (completed.replace("TASK_CHECK: pass", ""),
                       completed.replace("TASK_CHECK: pass", "> TASK_CHECK: pass"),
                       completed.replace("TASK_CHECK: pass", "```\nTASK_CHECK: pass\n```"),
                       completed.replace("TASK_CHECK: pass", "TASK_CHECK: pass\nTASK_CHECK: fail"),
                       completed.replace("작업 없음", "작업 있음"),
                       completed.replace("확인 완료·실행 중 작업 없음", "열어 둠")):
            with self.subTest(report=report):
                self.assertFalse(auto.check_finished(report))

    CLOSED = ("초안을 원문과 대조해 통과를 확인했습니다.\nTASK_CHECK: pass\n"
              "남은 것: 요유 몫: 초안을 읽고 보낼지 정하기 · 유요 몫: 리드가 초안 올리기 · 세션: 닫음")

    def lost_combined_report(self):
        """A finished check whose combined report pane-tell refused (2026-11-16): archived, failed, no receipt."""
        self.finish()
        self.tell_exit = 1
        self.finish(self.CLOSED, check=True)
        self.tell_exit = 0
        context_path = next((self.state / "automatic-checks").glob("*.json"))
        self.assertFalse(json.loads(context_path.read_text()).get("check_delivered"))
        return context_path

    def test_redeliver_check_rebuilds_the_lost_combined_report_once(self):
        context_path = self.lost_combined_report()
        lost = self.tells[-1]
        self.assertEqual(auto.redeliver_check("ac-" + context_path.stem, self.env), 0)
        self.assertEqual(self.tells[-1], lost, "the same combined report, rebuilt from the checker's archived report")
        self.assertIs(json.loads(context_path.read_text()).get("check_delivered"), True)
        self.assertEqual(auto.redeliver_check(context_path.stem, self.env), 0)
        self.assertEqual(len(self.tells), 2, "a delivered check is not sent again")

    def queue_in_main(self):
        tell_state = self.root / "state/pane-tell"
        (tell_state / "waiting").mkdir(parents=True, exist_ok=True)
        self.tell_out = "queued for default/w5:p3 (👑 test receiver): its input box is not empty, so this goes in once it is\n"
        self.on_tell = lambda message: (tell_state / "waiting/1.q").write_text("[pane-tell: x] " + message)
        return tell_state

    def test_queued_final_check_gets_its_receipt_only_once_typed_in(self):
        # Codex review: pane-tell exits 0 for a queued message too, before the main pane has it in front of it.
        self.finish()
        tell_state = self.queue_in_main()
        self.finish(self.CLOSED, check=True)
        context_path = next((self.state / "automatic-checks").glob("*.json"))
        self.assertFalse(json.loads(context_path.read_text()).get("check_delivered"), "queued is not delivered")
        self.assertEqual(self.tell_envs[-1].get("PANE_TELL_BATCH"), "0", "the final report is not held in a batch")
        self.assertEqual(Path(str(context_path) + ".redeliver").read_text(), "1.q")
        args, kwargs = self.finalizers[0]
        self.assertEqual(args[-2:], ["--redeliver-check", "ac-" + context_path.stem])
        self.assertTrue(kwargs.get("start_new_session"))
        self.assertEqual(list((self.root / "reports").parent.glob("state/pane-tell/failed-reports/*")), [],
                         "a queued report is not a lost one")
        self.finish(self.CLOSED, check=True)
        self.assertEqual(len(self.tells), 1, "a replayed Stop does not queue a second copy")
        # A permission-retried check waits on its queued copy the same way.
        context = json.loads(context_path.read_text())
        context["permission_retry"] = {"checker_address": "workers/w1:p10"}
        context_path.write_text(json.dumps(context))
        (tell_state / "log").write_text("2026-11-16 21:23:33 delivered 1.q to w5:p3\n")
        self.assertEqual(auto.redeliver_check(context_path.stem, self.env), 0)
        self.assertEqual(len(self.tells), 1, "the finalizer waits on the queued copy and sends nothing")
        self.assertIs(json.loads(context_path.read_text()).get("check_delivered"), True)
        self.assertFalse(Path(str(context_path) + ".redeliver").exists())

    def test_redeliver_check_runs_from_the_main_pane(self):
        context_path = self.lost_combined_report()
        self.assertEqual(auto.redeliver_check(context_path.stem, {**self.env, "HERDR_PANE_ID": self.main["pane_id"]}), 0)
        self.assertNotIn("HERDR_PANE_ID", self.tell_envs[-1], "pane-tell refuses a sender that is its own receiver")
        self.assertIs(json.loads(context_path.read_text()).get("check_delivered"), True)

    def test_redeliver_check_marks_a_queued_copy_only_once_it_is_typed_in(self):
        context_path = self.lost_combined_report()
        tell_state = self.queue_in_main()
        with patch.object(auto, "REDELIVER_WAIT", 0):
            self.assertEqual(auto.redeliver_check(context_path.stem, self.env), 1)
        self.assertFalse(json.loads(context_path.read_text()).get("check_delivered"), "queued is not delivered")
        (tell_state / "log").write_text("2026-11-16 21:23:33 delivered 1.q to w5:p3\n")
        self.assertEqual(auto.redeliver_check(context_path.stem, self.env), 0)
        self.assertEqual(len(self.tells), 2, "the rerun waits on the queued copy instead of sending another")
        self.assertIs(json.loads(context_path.read_text()).get("check_delivered"), True)

    def test_redeliver_check_never_sends_an_interim_or_unknown_check(self):
        self.finish()
        self.tell_exit = 1
        self.finish("질문: 어느 스레드에 올릴까요?\nTASK_CHECK: pass\n남은 것: 요유 몫: 답하기 · 세션: 닫음", check=True)
        self.tell_exit = 0
        sent = len(self.tells)
        context_path = next((self.state / "automatic-checks").glob("*.json"))
        self.assertEqual(auto.redeliver_check(context_path.stem, self.env), 1)
        self.assertEqual(auto.redeliver_check("0" * 16, self.env), 1)
        self.assertEqual(auto.redeliver_check("not-a-token", self.env), 2)
        self.assertEqual(len(self.tells), sent)
        self.assertFalse(json.loads(context_path.read_text()).get("check_delivered"))

    def test_closed_check_with_a_yoyoo_decision_is_the_final_report(self):
        # A draft in 요유's name always ends with 요유 몫 (whether to send it). A closed check with one TASK_CHECK marker is
        # the final report and gives the receipt; a question is still interim even when closed (lead, 2026-11-16).
        self.finish()
        self.finish("질문: 초안을 어느 스레드에 올릴까요?\nTASK_CHECK: pass\n"
                    "남은 것: 요유 몫: 질문에 답하기 · 유요 몫: 없음 · 세션: 닫음", check=True)
        context = json.loads(next((self.state / "automatic-checks").glob("*.json")).read_text())
        self.assertFalse(context.get("check_delivered"))
        self.assertIn("질문·대기 보고", self.tells[-1][1])
        self.finish("초안을 원문과 대조해 통과를 확인했습니다.\nTASK_CHECK: pass\n"
                    "남은 것: 요유 몫: 초안을 읽고 구디님께 보낼지 정하기 · 유요 몫: 리드가 초안 올리기 · 세션: 닫음", check=True)
        context = json.loads(next((self.state / "automatic-checks").glob("*.json")).read_text())
        self.assertIs(context.get("check_delivered"), True)
        self.assertIn("확인 칸의 마지막 보고", self.tells[-1][1])
        self.assertIn("요유 몫: 초안을 읽고 구디님께 보낼지 정하기", self.tells[-1][1])

    def test_completion_alias_still_relays_questions_and_user_decisions_as_interim(self):
        self.finish()
        for extra, left in (("질문: 어떤 방법으로 할까요?\n", "없음"), ("", "배포 승인")):
            report = (extra + "TASK_CHECK: pass\n남은 것: 요유 몫: " + left +
                      " · 세션: 확인 완료·실행 중 작업 없음")
            self.finish(report, check=True)
            context = json.loads(next((self.state / "automatic-checks").glob("*.json")).read_text())
            self.assertFalse(context.get("check_delivered"))
            self.assertIn("질문·대기 보고", self.tells[-1][1])
        self.finish("TASK_CHECK: pass\n남은 것: 요유 몫: 없음 · 세션: 확인 완료·실행 중 작업 없음", check=True)
        context = json.loads(next((self.state / "automatic-checks").glob("*.json")).read_text())
        self.assertIs(context.get("check_delivered"), True)

    def test_checker_prompt_defaults_to_one_nonproduction_check(self):
        self.finish()
        prompt = self.spawns[0][0][-1]
        self.assertIn("One completed automatic check", prompt)
        self.assertIn("Do not request another cross-review", prompt)
        self.assertIn("explicit user-mandated", prompt)
        self.assertIn("TASK_CHECK: pass|fail|unknown", prompt)
        self.assertIn("alone on its line with no other text on that line; put any explanation on the next line", prompt)

    def test_follow_up_uses_same_worker_and_gets_a_fresh_independent_check(self):
        self.finish()
        self.finish(CHECKED, check=True)
        self.finish(DONE.replace("12개", "13개"))
        self.assertNotIn("w1:p2", self.closed)
        self.assertEqual(len(self.spawns), 2)
        self.assertTrue(all("--check" in args for args, env in self.spawns))

    def test_combined_report_preserves_summaries_when_worker_moves(self):
        worker = DONE.replace("유요 몫: 없음", "유요 몫: 검토 대기") + "\nTASK_RESULT: pass"
        checked = CHECKED.replace("PASS", "FAIL").replace("유요 몫: 없음", "유요 몫: 실패 수정") + "\nTASK_CHECK: fail"
        self.finish(worker)
        self.worker["pane_id"] = "w2:p7"
        self.finish(checked, check=True)
        message = self.tells[-1][1]
        for item in (worker.splitlines()[0], checked.splitlines()[0], "검토 대기", "실패 수정",
                     "TASK_RESULT: pass", "TASK_CHECK: fail", "전체 보고:", "확인 전체 보고:"):
            self.assertIn(item, message)
        self.assertNotIn("세부 근거는 보존됩니다", message)
        self.assertIn("일 칸: workers/w2:p7", message)
        self.assertIn("확인 종류: codex (", message)

    def test_combined_report_caps_both_default_summaries(self):
        worker = "W" * 600 + "\nTest passed.\n" + DONE.splitlines()[-1]
        checked = "C" * 600 + "\nTest passed.\n" + CHECKED.splitlines()[-1]
        self.finish(worker)
        context = json.loads(next((self.state / "automatic-checks").glob("*.json")).read_text())
        self.assertEqual(context["summary"].splitlines()[0], "W" * 200)
        self.finish(checked, check=True)
        message = self.tells[-1][1]
        self.assertIn("W" * 200, message)
        self.assertNotIn("W" * 201, message)
        self.assertIn("C" * 200, message)
        self.assertNotIn("C" * 201, message)

    def test_job_report_caps_default_first_line(self):
        self.env["MUNICE_AUTO_DISPATCH"] = "1"
        self.finish("J" * 600 + "\n남은 것: 요유 몫: 승인 · 유요 몫: 없음 · 세션: 닫아도 됨")
        self.assertIn("J" * 200, self.tells[-1][1])
        self.assertNotIn("J" * 201, self.tells[-1][1])

    def test_stand_in_worker_report_caps_default_first_line(self):
        self.names["w1:p2"] = "spawn-absent-1"
        self.finish("S" * 600 + "\n" + DONE.splitlines()[-1])
        self.assertIn("S" * 200, self.tells[-1][1])
        self.assertNotIn("S" * 201, self.tells[-1][1])

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
        self.assertEqual(len(self.spawns), 2)
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
        self.assertEqual(len(self.wake_queries), 1)
        wake_env = self.wake_queries[0][1]
        for name in ("HERDR_SOCKET_PATH", "HERDR_PANE_ID", "HERDR_SESSION"):
            self.assertNotIn(name, wake_env, "main wake must not inherit the worker route")
        inbox_path = self.state / "alerts/state.json"
        inbox = json.loads(inbox_path.read_text())
        self.assertEqual(len(inbox["events"]), 1)
        alert = next(iter(inbox["events"].values()))
        self.assertEqual((alert["title"], alert["body"]), (title, body))
        self.assertIsNone(alert["receipt"], "enqueue and wake never acknowledge consumption")
        self.assertIsNone(inbox["claim"])
        self.assertEqual(inbox["receipts"], {})
        self.assertFalse((self.state / "alerts/wake.json").exists(), "no prompt without an active main runtime")
        self.tell_exit = 0
        self.finish(CHECKED, check=True)
        self.assertIn("w1:p9", self.closed)
        self.assertEqual(len(self.spawns), 1)
        self.assertEqual(len(self.tells), 2, "only the successful delivery is marked as delivered")
        self.assertEqual(len(self.alerts), 1, "successful retry does not send another failure alert")
        self.assertEqual(json.loads(inbox_path.read_text()), inbox,
                         "report retry must not duplicate, claim or consume the main alert")

    def test_slack_not_posted_report_keeps_codex(self):
        for word in ("Slack", "슬랙"):
            with self.subTest(word=word):
                self.finish(DONE + f"\n{word}에는 게시하지 않았습니다")
                self.assertTrue(self.spawns, "a result must open a check")
                args = self.spawns[-1][0]
                self.assertEqual(args[args.index("--kind") + 1], "codex")

    def test_network_or_process_evidence_keeps_codex(self):
        # 요유 2026-11-07: a Codex check pane has the network, gcloud and process reads, so no word sends it to Claude.
        for word in ("gcloud", "bq", "pgrep", "curl", "launchctl", "소켓", "네트워크"):
            with self.subTest(word=word):
                self.finish(DONE.replace("pytest", word) + f"\n{word}을 실행해 확인했습니다.")
                self.assertTrue(self.spawns, "a result must open a check")
                args = self.spawns[-1][0]
                self.assertEqual(args[args.index("--kind") + 1], "codex")

    def test_browser_requirement_keeps_codex_before_opening(self):
        self.finish(DONE + "\nego-browser skill로 실제 화면을 확인했습니다.")
        args = self.spawns[0][0]
        self.assertEqual(args[args.index("--kind") + 1], "codex")

    def test_named_process_test_keeps_codex_before_opening(self):
        self.worker['cwd'] = str(self.root)
        (self.root / 'process.test.py').write_text("import subprocess\nsubprocess.run(['ps', '-A'])\n")
        self.finish(DONE + "\npython3 process.test.py를 실행했습니다.")
        args = self.spawns[0][0]
        self.assertEqual(args[args.index("--kind") + 1], "codex")

    def test_variable_process_probe_keeps_codex_before_opening(self):
        path = self.root / '.local/state/codex-fast-probe.py'
        path.parent.mkdir(parents=True)
        path.write_text("import subprocess\ncommand = ['codex', 'app-server']\nsubprocess.Popen(command)\n")
        self.finish(DONE + f"\npython3 -B {path}\n")
        args = self.spawns[0][0]
        self.assertEqual(args[args.index('--kind') + 1], 'codex')

    BLOCKED = ("Process check is blocked.\nTASK_CHECK: unknown\n"
               "blocked_reason: SIGNAL_DENIED\nblocked_command: python3 process.test.py -> ps -A\n"
               "retry_target: claude\n남은 것: 요유 몫: 없음 · 유요 몫: 없음 · 세션: 닫음")

    def test_all_check_launches_require_fresh_source_without_changing_checkout(self):
        self.finish(DONE + "\nCompared source lines in the reported PR.")
        self.finish(self.BLOCKED, check=True)
        self.assertEqual(len(self.spawns), 2)
        for args, _ in self.spawns:
            with self.subTest(launcher=args[-2]):
                prompt = args[-1]
                self.assertIn("repository's instructed work branch", prompt)
                self.assertIn("remote default branch", prompt)
                self.assertIn("read the latest remote source without fetching", prompt)
                self.assertIn("git show origin/<branch>:<file>", prompt)
                self.assertIn("and local HEAD to identify an outdated checkout", prompt)
                self.assertIn("reported PR/commit separately", prompt)
                self.assertIn("Do not fetch, switch branches or update working files", prompt)
                self.assertIn("If a required code comparison cannot verify source freshness", prompt)
                self.assertIn("report `TASK_CHECK: unknown` with the failed read and blocker", prompt)
                self.assertIn("unless an independently verified defect requires `TASK_CHECK: fail`", prompt)
                self.assertIn("never mark that comparison verified from local HEAD alone", prompt)

    def test_investigation_check_does_not_require_unrelated_source_comparison(self):
        self.finish(DONE + "\nRead-only investigation without code changes.")
        prompt = self.spawns[0][0][-1]
        self.assertIn("When comparing code lines", prompt)
        self.assertIn("Skip source comparison when the task does not involve code lines", prompt)

    def test_freshness_checks_read_remote_without_fetching_in_all_launches(self):
        # A check cannot write the checked repository, so any fetch (even --no-write-fetch-head) fails once
        # the remote has new commits; ls-remote and gh api read the remote without writing.
        self.finish(DONE + "\nCompared source lines in the reported PR.")
        self.finish(self.BLOCKED, check=True)
        self.assertEqual(len(self.spawns), 2)
        for args, _ in self.spawns:
            with self.subTest(launcher=args[-2]):
                prompt = args[-1]
                self.assertIn("git ls-remote origin refs/heads/<branch>", prompt)
                self.assertIn("git rev-parse origin/<branch>", prompt)
                self.assertIn("gh api -H 'Accept: application/vnd.github.raw' 'repos/<owner>/<repo>/contents/<file>?ref=<branch>'", prompt)
                self.assertNotIn("git fetch --no-write-fetch-head", prompt)
                self.assertNotIn("Fetch that branch", prompt)

    def test_gcp_default_identity_guidance_reaches_both_runtime_launches(self):
        spec = importlib.util.spec_from_file_location(
            'gcp_guard', Path(__file__).parents[1] / 'claude/hooks/gcp-account-guard.py')
        guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(guard)
        for codex in (False, True):
            record = ({'type': 'response_item', 'payload': {
                'type': 'message', 'role': 'user', 'content': 'Read cloud resource status.'}}
                if codex else {'type': 'user', 'message': {'content': 'Read cloud resource status.'}})
            path = self.transcript([record], codex=codex)
            before = len(self.spawns)
            self.finish(DONE + f"\nCloud status check {codex}.")
            self.finish(self.BLOCKED, check_id=self.checker['pane_id'])
            launches = self.spawns[before:]
            self.assertEqual(len(launches), 2)
            self.assertEqual({args[args.index('--kind') + 1] for args, _ in launches},
                             {'codex'})
            for args, _ in launches:
                with self.subTest(codex=codex, launcher=args[-2]):
                    prompt = args[-1]
                    self.assertIn("Run GCP commands one at a time using the default gcloud configuration", prompt)
                    self.assertIn("without account, configuration, or impersonation overrides", prompt)
                    self.assertIsNone(guard.tool_problem({
                        'tool_name': 'Bash', 'tool_input': {'command': shlex.join(args)}}, {}))
            path.unlink()

    def test_permission_unknown_opens_scoped_codex_retry(self):
        self.env['HERDR_TASK_ID'] = 'job-test-retry'
        self.finish()
        self.finish(self.BLOCKED, check=True)
        self.assertEqual(len(self.spawns), 2)
        self.assertEqual(self.tells, [])
        args, env = self.spawns[-1]
        self.assertEqual(args[args.index('--kind') + 1], 'codex')
        self.assertEqual(args[args.index('--job-id') + 1], 'job-test-retry')
        self.assertEqual(env['HERDR_PANE_ID'], self.main['pane_id'])
        self.assertIn('only the permission-blocked portion', args[-1])
        self.assertIn('ps -A', args[-1])
        self.assertIn("alone on its line with no other text on that line; put any explanation on the next line", args[-1])
        self.finish(self.BLOCKED, check=True)
        self.assertEqual(len(self.spawns), 2, 'replayed first report cannot start another checker')
        self.finish(CHECKED, check_id='w1:p10')
        self.assertEqual(len(self.tells), 1)
        for value in ('Process check is blocked.', CHECKED.splitlines()[0], 'TASK_CHECK: unknown'):
            self.assertIn(value, self.tells[0][1])
        self.finish(CHECKED, check_id='w1:p10')
        self.assertEqual(len(self.tells), 1)

    def test_permission_retry_works_for_both_runtime_payloads_and_open_session_replays(self):
        self.finish()
        for codex in (False, True):
            transcript = self.root / 'check.jsonl'
            transcript.write_text(json.dumps({"type": "assistant", "message": {
                "content": [{"type": "text", "text": self.BLOCKED}]}}))
            payload = ({"last_assistant_message": self.BLOCKED} if codex else
                       {"transcript_path": str(transcript)})
            for _ in range(2):
                report = auto.decide(payload, {**self.env, 'HERDR_PANE_ID': 'w1:p9'})
                self.finish(report, check=True)
        self.assertEqual(len(self.spawns), 2)
        self.assertFalse(self.tells)
        # Correlation still works after the retry pane loses its original label.
        self.checkers[-1]['label'] = '🟢 🫡 renamed check'
        self.finish(CHECKED, check_id='w1:p10')
        self.assertEqual(len(self.tells), 1)

    def test_permission_retry_does_not_bypass_interim_or_incomplete_reports(self):
        for text in (self.BLOCKED.replace('세션: 닫음', '세션: 실행 중'),
                     self.BLOCKED.replace('retry_target: claude', ''),
                     self.BLOCKED.replace('요유 몫: 없음', '요유 몫: production approval')):
            self.finish(DONE + text[:30])
            before = len(self.spawns)
            self.finish(text, check_id=self.checker['pane_id'])
            self.assertEqual(len(self.spawns), before)

    def test_existing_claude_check_does_not_start_another_runtime_retry(self):
        self.finish()
        path = next((self.state / 'automatic-checks').glob('*.json'))
        context = json.loads(path.read_text())
        context['kind'] = 'claude'  # A check already running before this policy change.
        context['actual_runner'] = 'claude'
        path.write_text(json.dumps(context))
        self.finish(self.BLOCKED, check=True)
        self.assertEqual(len(self.spawns), 1)
        self.assertEqual(len(self.tells), 1)

    def test_permission_retry_arguments_reach_real_launcher(self):
        self.finish()
        self.finish(self.BLOCKED, check=True)
        args = self.spawns[-1][0]
        fixture = Path(__file__).with_name('pane-spawn-test-fixture.sh')
        with patch.object(subprocess, 'Popen', REAL_POPEN):
            result = REAL_RUN(['bash', '-c', 'source "$0"; workers_up; is_main; ps "$@"',
                               str(fixture), *args[1:]], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'workers/w1:new')

    def test_permission_retry_resumes_persisted_intent_after_interruption(self):
        self.finish()
        context_path = next((self.state / "automatic-checks").glob("*.json"))
        context = json.loads(context_path.read_text())
        context['permission_retry'] = dict(summary=auto.short_report(self.BLOCKED), report='check.md', entry='entry',
            verdict='unknown', **auto.check_permissions.blockage(self.BLOCKED))
        context_path.write_text(json.dumps(context))
        self.finish(self.BLOCKED, check=True)
        self.assertEqual(len(self.spawns), 2)
        checked = CHECKED.replace('PASS', 'PASS\nTASK_CHECK: pass')
        self.finish(checked, check_id='w1:p10')
        self.assertIn('Combined TASK_CHECK: unknown', self.tells[0][1])

    def test_legacy_context_recovers_worker_recording_restrictions(self):
        self.transcript([{"type": "user", "message": {"content": "Do not save this in memory."}}])
        self.finish()
        context_path = next((self.state / "automatic-checks").glob("*.json"))
        context = json.loads(context_path.read_text())
        context.pop('recording_policy')
        context_path.write_text(json.dumps(context))
        self.finish(self.BLOCKED, check=True)
        self.assertIn('원 작업의 기록 금지 제한', self.spawns[-1][0][-1])
        self.assertIn('memory', self.spawns[-1][0][-1])

    def test_legacy_context_without_source_forbids_persistent_writes(self):
        self.finish()
        context_path = next((self.state / "automatic-checks").glob("*.json"))
        context = json.loads(context_path.read_text())
        context.pop('recording_policy')
        context_path.write_text(json.dumps(context))
        self.finish(self.BLOCKED, check=True)
        self.assertIn('Original recording restrictions unavailable', self.spawns[-1][0][-1])
        self.assertIn('Do not write memory, issues, board comments, work logs or documentation', self.spawns[-1][0][-1])

    def test_permission_retry_retains_original_recording_restrictions(self):
        self.transcript([{"type": "user", "message": {"content": "Do not save this in memory."}}])
        self.finish()
        self.finish(self.BLOCKED, check=True)
        self.assertIn('memory', self.spawns[-1][0][-1])
        self.assertIn('원 작업의 기록 금지 제한', self.spawns[-1][0][-1])

    def test_scoped_retry_pass_does_not_promote_unrelated_unknowns(self):
        self.finish()
        self.finish(self.BLOCKED.replace('Process check is blocked.',
            'Original source unavailable; coverage unknown. Process check is blocked.'), check=True)
        self.finish(CHECKED.replace('PASS', 'PASS\nTASK_CHECK: pass'), check_id='w1:p10')
        self.assertIn('Combined TASK_CHECK: unknown', self.tells[0][1])
        self.assertNotIn('Combined TASK_CHECK: pass', self.tells[0][1])

    def test_permission_retry_preserves_independent_failure(self):
        self.finish()
        self.finish(self.BLOCKED.replace('TASK_CHECK: unknown', 'TASK_CHECK: fail'), check=True)
        self.finish(CHECKED, check_id='w1:p10')
        self.assertIn('Combined TASK_CHECK: fail', self.tells[0][1])
        self.assertEqual(len(self.spawns), 2)

    def test_permission_retry_failure_delivers_both_reports_without_recursing(self):
        self.finish()
        self.finish(self.BLOCKED, check=True)
        self.finish(self.BLOCKED.replace('Process check', 'Retry check'), check_id='w1:p10')
        self.assertEqual(len(self.spawns), 2)
        self.assertEqual(len(self.tells), 1)
        self.assertIn('Process check is blocked.', self.tells[0][1])
        self.assertIn('Retry check is blocked.', self.tells[0][1])

    def test_permission_retry_failed_delivery_uses_same_checker(self):
        self.finish()
        self.finish(self.BLOCKED, check=True)
        self.tell_exit = 1
        self.finish(CHECKED, check_id='w1:p10')
        self.tell_exit = 0
        self.finish(CHECKED, check_id='w1:p10')
        self.assertEqual(len(self.spawns), 2)
        self.assertEqual(len(self.tells), 2)
        self.assertEqual(self.tells[0][1], self.tells[1][1])

    def test_permission_retry_start_failure_reports_original(self):
        self.finish()
        self.spawn_error = 'launcher failed'
        self.finish(self.BLOCKED, check=True)
        self.assertEqual(len(self.tells), 1)
        self.assertIn('Process check is blocked.', self.tells[0][1])
        self.assertIn('launcher failed', self.tells[0][1])
        count = len(self.spawns)
        self.finish(self.BLOCKED, check=True)
        self.assertEqual(len(self.spawns), count)

    def test_permission_retry_keeps_lead_and_safety_blocks_on_existing_route(self):
        for block in (self.BLOCKED.replace('retry_target: claude', 'retry_target: lead'),
                      self.BLOCKED.replace('SIGNAL_DENIED', 'OTHER_DENIED').replace(
                          'python3 process.test.py -> ps -A', 'change security hook trust settings')):
            self.finish(DONE + block[:30])
            before = len(self.spawns)
            self.finish(block, check=True, check_id=self.checker['pane_id'])
            self.assertEqual(len(self.spawns), before)

    def test_independent_failure_with_blockage_remains_fail(self):
        text = ("실제 assertion 실패도 있습니다.\nTASK_CHECK: fail\nblocked_reason: BROWSER_DENIED\n"
                "blocked_command: ego-browser skill\nretry_target: claude\n남은 것: 세션: 닫음")
        self.assertIn('TASK_CHECK: fail', auto.short_report(text))
        self.assertIn('blocked_reason: BROWSER_DENIED', auto.short_report(text))

    def test_later_success_clears_interim_permission_fields(self):
        self.finish()
        self.finish('확인 진행 중입니다.\nblocked_reason: OTHER_DENIED\nblocked_command: crontab -l\n'
                    'retry_target: claude\n남은 것: 요유 몫: 없음 · 유요 몫: 추가 확인 · 세션: 열어 둠', check=True)
        self.finish('확인을 마쳤습니다.\nTASK_CHECK: pass\n남은 것: 요유 몫: 없음 · 유요 몫: 없음 · 세션: 닫음', check=True)
        saved = json.loads(next((self.state / 'automatic-checks').glob('*.json')).read_text())
        self.assertNotIn('blocked_reason', saved)

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

    # api#1218: a figure, a draft or numbers to post reached 요유 unchecked through the direct route.
    FIGURE = ("그림 PNG 한 장과 설명 5줄을 만들었고, Slack에는 올리지 않았습니다.\nTASK_RESULT: pass\n"
              "남은 것: 요유 몫: 없음 · 유요 몫: 메인 세션이 PNG와 설명 글을 스레드에 올리기 · 세션: 이 세션은 일이 끝났고 닫아도 됩니다")
    DRAFT = ("조사를 마쳤고, 보낼 답 초안(6줄)과 새 그림을 만들었습니다.\n"
             "남은 것: 요유 몫: 다음 단계 진행 여부 결정 · 유요 몫: 메인 세션이 그림과 답 초안을 스레드에 올리기 · 세션: 닫아도 됨")
    NUMBERS = ("월 비용을 다시 계산했습니다. 월 13만 원입니다.\n"
               "남은 것: 요유 몫: 없음 · 유요 몫: 메인 세션이 이 숫자를 요유 스레드에 올리기 · 세션: 열어 둠")

    def main_blocks(self, message, said="모아 두었습니다.\n남은 것: 요유 몫: 없음 · 유요 몫: 없음 · 세션: 닫아도 됨", entries=None):
        transcript = self.root / "main.jsonl"
        entries = entries or [{"type": "user", "message": {"role": "user", "content": message}}]
        transcript.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries))
        with patch.object(auto.share, "delegates_checks", return_value=True):
            return auto.share.relay_reason({"transcript_path": str(transcript)}, said, self.env)

    def test_deliverables_open_a_check_whatever_their_direct_reason(self):
        for text in (self.FIGURE, self.DRAFT, self.NUMBERS):
            with self.subTest(text=text.splitlines()[0]):
                self.tells.clear()
                self.spawns.clear()
                self.prechecks.clear()
                self.finish(text)
                self.assertEqual(len(self.spawns), 1, "a deliverable for 요유 goes to a check pane")
                self.assertEqual(self.tells, [], "before the check reports, nothing else reaches the main pane")
                self.assertEqual(len(self.prechecks), 1, "only the 확인 전 보고, which backs no 확인: line")

    def test_estimate_line_does_not_hide_a_finished_deliverable(self):
        # api#1218 check: an estimate line on top of a finished figure report sent it straight to the main pane.
        self.finish("끝날 예상: 21:30 KST\n" + self.FIGURE)
        self.assertEqual(len(self.spawns), 1)
        self.assertEqual(self.tells, [])

    def test_a_finished_figure_waiting_for_approval_opens_a_check(self):
        # Codex review of 675d502: waiting is no work under way.
        self.finish("그림 PNG를 만들었고 게시 승인을 기다리고 있습니다.\n남은 것: 요유 몫: 게시 승인 · 세션: 열어 둠")
        self.assertEqual(len(self.spawns), 1)

    def test_work_left_in_the_remaining_line_is_no_finished_sentence(self):
        # Codex review of 675d502: 남은 것's "그림 수정" is the work left, not work done.
        self.finish("끝날 예상: 21:30 KST\n그림을 수정 중입니다.\n남은 것: 요유 몫: 없음 · 유요 몫: 그림 수정 · 세션: 열어 둠")
        self.assertEqual(self.spawns, [])
        self.assertTrue(self.tells[0][1].startswith("바로 전달하는 칸의 보고입니다("), self.tells[0][1])

    def test_a_question_after_a_computed_amount_does_not_hide_it(self):
        # Codex review of 2efb703
        self.finish("월 비용을 다시 계산했습니다. 월 5만 원입니다.\n질문: 다음 단계로 진행할까요?\n"
                    "남은 것: 요유 몫: 진행 여부 · 세션: 열어 둠")
        self.assertEqual(len(self.spawns), 1)

    def test_progress_notes_with_a_finished_side_step_stay_direct(self):
        # Codex review of 2efb703: a multi-line 남은 것 block, and a finished sentence that is not the figure.
        for text in ("그림을 수정 중입니다.\n남은 것:\n- 요유 몫: 없음\n- 유요 몫: 그림 수정\n- 세션: 열어 둠",
                     "자료를 확인했습니다.\n그림을 그리는 중입니다.\n남은 것: 요유 몫: 없음 · 유요 몫: 그림 마무리 · 세션: 열어 둠"):
            with self.subTest(text=text.splitlines()[0]):
                self.tells.clear()
                self.spawns.clear()
                self.finish(text)
                self.assertEqual(self.spawns, [])
                self.assertTrue(self.tells[0][1].startswith("바로 전달하는 칸의 보고입니다("), self.tells[0][1])

    def test_counts_computed_open_a_check(self):
        # api#1218 check of 952c627: counts with a unit (대, 개, 건) are numbers to post too.
        for text in ("계산을 마쳤습니다. 최대 인스턴스는 12대, 최소 인스턴스는 3대입니다.\n"
                     "남은 것: 요유 몫: 없음 · 유요 몫: 없음 · 세션: 열어 둠",
                     "pytest 12개 통과를 확인했습니다. Slack에는 올리지 않았습니다.\n"
                     "남은 것: 요유 몫: 머지 결정 · 유요 몫: PR 올리기 · 세션: 닫아도 됨"):
            with self.subTest(text=text.splitlines()[0]):
                self.tells.clear()
                self.spawns.clear()
                self.assertEqual(auto.direct_reason(text), "")
                self.finish(text)
                self.assertEqual(len(self.spawns), 1)
                self.assertEqual(self.tells, [])

    def test_numbers_answered_without_a_post_request_open_a_check(self):
        # api#1218 check: a computed figure counted only when the report asked to post it.
        for text in ("비용 계산 결과를 정리했습니다. 월 5만 원입니다.\n남은 것: 요유 몫: 없음 · 유요 몫: 없음 · 세션: 열어 둠",
                     "전환율을 다시 구했습니다. 3.2%입니다.\n남은 것: 요유 몫: 다음 실험 결정 · 세션: 닫아도 됨"):
            with self.subTest(text=text.splitlines()[0]):
                self.tells.clear()
                self.spawns.clear()
                self.finish(text)
                self.assertEqual(len(self.spawns), 1)
                self.assertEqual(self.tells, [])

    def test_deliverable_whose_check_cannot_start_makes_main_open_one(self):
        self.spawn_error = "workers unavailable"
        self.finish(self.FIGURE)
        self.assertEqual(len(self.tells), 1)
        message = self.tells[0][1]
        self.assertTrue(message.startswith("이 칸의 보고입니다("), message)
        self.assertIn(auto.share.UNOPENED_NOTE, message)
        self.assertIn("workers unavailable", message)
        self.assertEqual(self.main_blocks(message), auto.share.UNOPENED_REASON)

    # 요유 2026-11-20 (#0 1795159819.211999, 1795159868.667869): A check that could not open
    # held a figure for two days. Nothing waits for a check pane now: main looks again with a command and a `확인:` line, or hands a
    # read-only recheck to an ordinary worker named `[rc-<ac id>]`, whose report comes straight back.
    POST_CHANNEL = "/Users/yoyoo/work/munice/munice-claude/.agents/skills/agent-board/main/main-channel.py"
    SAID = "다시 봤습니다.\n확인: 메인 세션이 `gh api`로 목록을 읽어 12개를 봤습니다.\n남은 것: 요유 몫: 없음 · 유요 몫: 없음 · 세션: 열어 둠"
    SAID_NO_LINE = "다시 봤습니다.\n남은 것: 요유 몫: 없음 · 유요 몫: 없음 · 세션: 열어 둠"

    def unopened_message(self, why=None):
        self.spawn_error = "workers unavailable"
        self.finish(self.FIGURE)
        message = self.tells[0][1]
        return message.replace(auto.share.UNOPENED_WHY, why) if why else message

    def key_of(self, message):
        return re.search(r"일 칸: \S+ \[ac-([0-9a-f]{16})\]", message)[1]

    def after_blocks(self, message, command, output="", said=None, is_error=False):
        """main_blocks with one tool call of the turn after the relayed report, and its result."""
        entries = [{"type": "user", "message": {"role": "user", "content": message}},
                   {"type": "assistant", "message": {"content": [
                       {"type": "tool_use", "id": "c1", "name": "Bash", "input": {"command": command}}]}},
                   {"type": "user", "message": {"content": [
                       {"type": "tool_result", "tool_use_id": "c1", "is_error": is_error, "content": output}]}}]
        return self.main_blocks(message, said or self.SAID, entries)

    def test_unopened_check_tells_main_how_to_look_again(self):
        message = self.unopened_message()
        for part in ("pane-spawn --kind auto", "[rc-", "읽기 전용", "직접 실행", "운영 쓰기 승인", "'확인:' 줄", "열지 못했다는 말은 쓰지 마세요",
                     "팀 스레드·돈·운영 글"):
            self.assertIn(part, message)
        self.assertIn(auto.share.UNOPENED_NOTE, message)
        self.assertNotIn("pane-spawn --check", message, "a check pane is not what to open")
        self.assertNotIn("'확인 중:'", message, "main-channel refuses the old head")
        self.assertEqual(self.main_blocks(message), auto.share.UNOPENED_REASON, "nothing looked at again yet")
        context = json.loads((self.state / "automatic-checks" / (self.key_of(message) + ".json")).read_text())
        self.assertTrue(context["fallback_delivered"], "the [ac-ID] in the message is the receipt main-channel reads")

    def test_note_goes_only_where_the_check_could_not_open(self):
        for why in (auto.share.UNOPENED_WHY, auto.share.LINK_FAILED_WHY):
            self.assertIn(auto.share.UNOPENED_NOTE, auto.unchecked_head(self.FIGURE, why))
        waiting = auto.unchecked_head(self.FIGURE, "자동 칸이 요유를 기다립니다")
        self.assertNotIn(auto.share.UNOPENED_NOTE, waiting, "an automatic pane that waits for 요유 keeps its check")
        self.assertIn(auto.share.DELEGATE_NOTE, waiting)

    def test_main_that_looked_again_may_answer(self):
        for why in (None, auto.share.LINK_FAILED_WHY):
            message = self.unopened_message(why)
            self.assertIsNone(self.after_blocks(message, "gh api repos/munice-dev/x/issues"), "a command ran and `확인:` says what it saw")
            self.assertEqual(self.after_blocks(message, "gh api repos/munice-dev/x/issues", said=self.SAID_NO_LINE), auto.share.UNOPENED_REASON,
                             "a command with no `확인:` line")
            self.assertEqual(self.after_blocks(message, "echo gh api"), auto.share.UNOPENED_REASON, "a chore is no look")
            self.assertEqual(self.after_blocks(message, "gh api x", output="PreToolUse:Bash hook error: blocked", is_error=True),
                             auto.share.UNOPENED_REASON, "a call a hook stopped never ran")

    def test_main_that_hands_a_recheck_out_may_wait(self):
        message = self.unopened_message()
        key = self.key_of(message)
        spawn = f'pane-spawn --kind auto "그림 재확인 [rc-{key}]" "읽기 전용으로 다시 볼 것"'
        waiting = "재확인을 맡겼습니다.\n남은 것: 요유 몫: 없음 · 유요 몫: 재확인 보고 받아 올리기 · 세션: 열어 둠"
        self.assertIsNone(self.after_blocks(message, spawn, output="default/w1:p4\n", said=waiting))
        self.assertIsNone(self.after_blocks(message, f"cd /tmp && {spawn}", output="default/w1:p4\n", said=waiting))
        for name, command, output in (("no rc name", 'pane-spawn --kind auto "그림 재확인" "다시 볼 것"', "default/w1:p4\n"),
                                      ("spawn failed", spawn, "pane-spawn: usage\n"),
                                      ("an echo", f"echo {spawn}", "")):
            with self.subTest(name):
                self.assertEqual(self.after_blocks(message, command, output=output, said=waiting), auto.share.UNOPENED_REASON)

    def test_an_early_post_alone_holds_the_result_back_no_more_than_a_look(self):
        message = self.unopened_message()
        command = f"python3 -I {self.POST_CHANNEL} post --thread 1794000000.000100 - <<'EOF'\n작업 중(확인 중): 그림을 올립니다.\n끝날 예상: 16:00 KST\nEOF"
        self.assertEqual(self.after_blocks(message, command, output="1794000002.000300\n", said=self.SAID_NO_LINE), auto.share.UNOPENED_REASON,
                         "the early answer is allowed beside a recheck, not instead of one")

    def test_a_check_that_never_tried_keeps_its_demand(self):
        waiting = self.unopened_message("자동 칸이 요유를 기다립니다")
        self.assertEqual(self.after_blocks(waiting, "gh api x"), auto.share.DELEGATE_REASON)
        # a report body that quotes the phrase does not turn its head into one
        quoted = self.unopened_message("자동 칸이 요유를 기다립니다") + f" 본문: {auto.share.UNOPENED_WHY}"
        self.assertEqual(self.after_blocks(quoted, "gh api x"), auto.share.DELEGATE_REASON)
        # one batched prompt: the way out covers the heads that could not open, not a waiting one next to them
        batch = self.unopened_message() + "\n" + waiting
        self.assertEqual(self.after_blocks(batch, "gh api x"), auto.share.DELEGATE_REASON)

    def test_a_recheck_worker_report_comes_straight_back(self):
        key = self.key_of(self.unopened_message())
        self.tells.clear()
        self.spawns.clear()
        self.worker["label"] = f"🟢 🫡 그림 재확인 [rc-{key}]"
        self.worker["agent_session"] = {"value": "sid-recheck"}  # another pane's report: its own receipt key
        self.finish(self.FIGURE + "\n재확인 쪽 보고")
        self.assertEqual(self.spawns, [], "a recheck of a check is no check: no loop")
        message = self.tells[0][1]
        self.assertTrue(message.startswith("바로 전달하는 칸의 보고입니다(읽기 전용 재확인입니다)"), message)
        self.assertIsNone(self.main_blocks(message), "main posts from it, as from a check pane's report")

    def test_a_made_up_or_opened_rc_token_buys_no_direct_route(self):
        # review of #119: the token counts only for a check that really could not open
        key = self.key_of(self.unopened_message())
        self.tells.clear()
        self.spawns.clear()
        self.worker["label"] = "🟢 🫡 매출 집계 [rc-0000000000000000]"
        self.finish(self.FIGURE + "\n가짜 토큰")
        self.assertTrue(self.spawns, "no context for that id: the report still tries its check")
        self.assertTrue(self.tells[0][1].startswith("이 칸의 보고입니다("), self.tells[0][1])
        path = self.state / "automatic-checks" / (key + ".json")
        context = json.loads(path.read_text())
        context["handed"] = True  # a check pane was started for it after all
        path.write_text(json.dumps(context))
        self.tells.clear()
        self.spawns.clear()
        self.worker["label"] = f"🟢 🫡 매출 집계 [rc-{key}]"
        self.finish(self.FIGURE + "\n열린 확인")
        self.assertTrue(self.spawns)
        self.assertTrue(self.tells[0][1].startswith("이 칸의 보고입니다("), self.tells[0][1])

    def test_the_rc_token_must_sit_in_the_pane_name_not_the_instruction(self):
        key = self.key_of(self.unopened_message())
        message = self.unopened_message()
        said = "재확인을 맡겼습니다.\n남은 것: 요유 몫: 없음 · 유요 몫: 재확인 보고 받아 올리기 · 세션: 열어 둠"
        inside = f'pane-spawn --kind auto "그림 재확인" "읽기 전용으로 [rc-{key}] 다시 볼 것"'
        self.assertEqual(self.after_blocks(message, inside, output="default/w1:p4\n", said=said), auto.share.UNOPENED_REASON)
        named = f'pane-spawn --kind auto --cwd /tmp "그림 재확인 [rc-{key}]" "다시 볼 것"'
        self.assertIsNone(self.after_blocks(message, named, output="default/w1:p4\n", said=said))

    def test_a_check_pane_main_opens_by_hand_is_a_check_too(self):
        message = self.unopened_message()
        said = "확인 칸을 열었습니다.\n남은 것: 요유 몫: 없음 · 유요 몫: 확인 보고 받아 올리기 · 세션: 열어 둠"
        self.assertIsNone(self.after_blocks(message, 'pane-spawn --check "그림" "다시 볼 것"', output="default/w1:p4\n", said=said))

    def test_the_link_failed_stub_never_replaces_a_context_a_check_may_hold(self):
        # review of #119: an OSError after the check started must not erase handed/checker records
        folder = self.state / "automatic-checks"
        folder.mkdir(parents=True, exist_ok=True)
        key = auto.check_key(self.worker, self.FIGURE)
        (folder / (key + ".json")).write_text(json.dumps({"handed": True, "checker_address": "workers/w1:p9"}))
        with patch.object(auto, "route_main_report", side_effect=OSError("disk")):
            self.finish(self.FIGURE)
        self.assertEqual(json.loads((folder / (key + ".json")).read_text())["checker_address"], "workers/w1:p9")

    def test_job_note_leaves_decisions_on_the_board_not_in_a_collected_list(self):
        # 요유 2026-11-19: no decision list is kept or shown any more; the board and the work thread carry the question once.
        self.assertNotIn("모아 둔 결정", auto.share.JOB_NOTE)
        self.assertIn("보드", auto.share.JOB_NOTE)

    def test_deliverable_from_a_waiting_job_tab_asks_for_a_check(self):
        self.env["MUNICE_AUTO_DISPATCH"] = "1"
        self.finish(self.DRAFT)
        message = self.tells[-1][1]
        self.assertNotIn(auto.share.JOB_NOTE, message)
        self.assertEqual(self.main_blocks(message), auto.share.DELEGATE_REASON)

    def test_reports_with_nothing_to_post_stay_direct(self):
        for text in ("질문: 큰 시험을 내일 할까요, 다음 주에 할까요? 비용 차이는 월 2만 원입니다.\n"
                     "남은 것: 요유 몫: 시험 시점 결정 · 유요 몫: 없음 · 세션: 닫아도 됨",
                     "끝날 예상: 18:30 KST\n자료를 확인했고 그림을 그리는 중입니다.\n"
                     "남은 것: 요유 몫: 없음 · 유요 몫: 그림 마무리 · 세션: 열어 둠",
                     # a time, a version or an issue number before a word is no count or amount
                     "20:30 회차를 돌렸고, v1.26.1 배포와 tf#1133 대조를 마쳤으며, api#889 원인을 찾았습니다.\n"
                     "남은 것: 요유 몫: 없음 · 유요 몫: 없음 · 세션: 열어 둠",
                     "pytest가 모두 통과하는 것을 확인했습니다. Slack에는 올리지 않았습니다.\n"
                     "남은 것: 요유 몫: 머지 결정 · 유요 몫: PR 올리기 · 세션: 닫아도 됨",
                     "스레드를 읽고 정할 것을 정리했습니다. 서버 답은 다른 세션 몫이라 초안은 만들지 않았습니다.\n"
                     "남은 것: 요유 몫: 결정 6개 · 유요 몫: 없음 · 세션: 열어 둠"):
            with self.subTest(text=text.splitlines()[0]):
                self.tells.clear()
                self.spawns.clear()
                self.finish(text)
                self.assertEqual(self.spawns, [])
                self.assertTrue(self.tells[0][1].startswith("바로 전달하는 칸의 보고입니다("), self.tells[0][1])
                self.assertIsNone(self.main_blocks(self.tells[0][1]))

    def test_waiting_job_without_deliverable_keeps_job_note(self):
        self.env["MUNICE_AUTO_DISPATCH"] = "1"
        self.finish("질문: A와 B 중 무엇으로 할까요?\n남은 것: 요유 몫: A 또는 B 결정 · 유요 몫: 없음 · 세션: 닫아도 됨")
        self.assertIn(auto.share.JOB_NOTE, self.tells[-1][1])

    def test_check_pane_question_about_a_draft_is_not_checked_again(self):
        self.finish(self.DRAFT)
        self.finish("초안 문장 3개를 고쳐야 합니다. 질문: 고친 뒤 올릴까요?\n"
                    "남은 것: 요유 몫: 게시 결정 · 세션: 닫으면 답 대기 멈춤", check=True)
        self.assertEqual(len(self.spawns), 1, "no check of a check")
        message = self.tells[0][1]
        self.assertTrue(message.startswith("바로 전달하는 칸의 보고입니다(확인 칸의"), message)
        self.assertIsNone(self.main_blocks(message))

    def test_direct_report_does_not_trigger_missing_checker_guard(self):
        self.finish("질문: 어느 안을 고를까요?\n남은 것: 요유 몫: A 또는 B 결정 · 세션: 닫아도 됨")
        transcript = self.root / "main.jsonl"
        transcript.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": self.tells[0][1]}},
                                         ensure_ascii=False) + "\n")
        report = "모아 두었습니다.\n남은 것: 요유 몫: A 또는 B 결정 · 유요 몫: 없음 · 세션: 닫아도 됨"
        with patch.object(auto.share, "delegates_checks", return_value=True):
            self.assertIsNone(auto.share.relay_reason({"transcript_path": str(transcript)}, report, self.env))


if __name__ == "__main__":
    unittest.main(argv=[__file__] if sys.argv[1:] == ['--selftest'] else None)
