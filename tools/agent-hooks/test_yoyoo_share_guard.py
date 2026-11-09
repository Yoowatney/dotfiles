"""Regression tests for unanswered thread calls; no live herdr or Slack access."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location("share_guard", Path(__file__).with_name("yoyoo-share-guard.py"))
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)

CALL = ("[Slack 스레드 요유 부름] #team https://example.slack.com/archives/CTEAM/p1794000000000100 "
        "(스레드 CTEAM/1794000000.000100, 글 CTEAM/1794000001.000200) 요유 글: 확인 부탁해요")
POST = "python3 /skills/main-channel.py post /tmp/reply.md"


def incoming(text=CALL):
    return {"type": "user", "message": {"role": "user", "content": text}}


def call(command=POST, name="Bash", ident="post", **arguments):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": ident, "name": name, "input": arguments or {"command": command}}]}}


def result(text="1794000002.000300\n", error=False, ident="post"):
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": ident, "is_error": error, "content": text}]}}


class ProseTests(unittest.TestCase):
    """A report that mentions a fence in a sentence still has its `남은 것:` line (19:03 report of 2026-11-07 was dropped)."""
    LEFT = "남은 것: 요유 몫: 없음 · 유요 몫: 없음 · 세션: 닫아도 됨"

    def test_a_fence_in_the_middle_of_a_line_opens_no_code(self):
        for text in ("코드 블록(``` 와 `인라인`)은 그대로 둡니다.\n- 둘째 줄\n\n" + self.LEFT,
                     "끝났습니다. ~~~ 는 물결 세 개입니다.\n" + self.LEFT):
            self.assertIn("남은 것:", guard.prose(text))

    def test_a_real_fence_still_hides_what_is_inside_it(self):
        text = "명령입니다.\n```\n남은 것: 요유 몫: 없음\n```\n" + self.LEFT
        self.assertEqual(guard.prose(text).count("남은 것:"), 1)
        self.assertIn("남은 것:", guard.prose("```\n안\n```\n  ```\n들여 쓴 코드\n  ```\n" + self.LEFT))
        self.assertNotIn("남은 것:", guard.prose("보고입니다.\n```\n열고 닫지 않은 코드\n" + self.LEFT))  # unclosed, as before


class ThreadCallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        state = self.root / ".local/state/orchestrator"
        state.mkdir(parents=True)
        (state / "main").write_text("main-terminal")
        fake = self.root / "herdr"
        fake.write_text('#!/bin/sh\ncat "' + str(self.root / "listing") + '.$1"\n')
        fake.chmod(0o755)
        self.env = {"HOME": str(self.root), "HERDR_BIN_PATH": str(fake), "CLAUDE_CODE_ENTRYPOINT": "cli"}
        self.panes()

    def panes(self, terminal="main-terminal"):
        (self.root / "listing.pane").write_text(json.dumps({"result": {"panes": [
            {"pane_id": "w1:p1", "terminal_id": terminal, "agent_session": {"value": "session"}}]}}))
        (self.root / "listing.agent").write_text('{"result":{"agents":[]}}')

    def reason(self, entries, **overrides):
        transcript = self.root / "session.jsonl"
        transcript.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in entries))
        payload = {"session_id": "session", "transcript_path": str(transcript),
                   "last_assistant_message": "", "stop_hook_active": False, **overrides}
        return guard.decide(payload, self.env)

    def test_unanswered_call_returns_turn(self):
        self.assertIn("CTEAM/1794000000.000100", self.reason([incoming()]) or "")

    def test_queued_call_returns_turn(self):
        queued = {"type": "attachment", "attachment": {"type": "queued_command", "prompt": CALL}}
        self.assertIsNotNone(self.reason([queued]))

    def test_pane_tell_wrapper_returns_turn(self):
        self.assertIsNotNone(self.reason([incoming("[pane-tell: relay] " + CALL)]))

    def test_chitchat_second_stop_passes(self):
        self.assertIsNotNone(self.reason([incoming(CALL.replace("확인 부탁해요", "ㅋㅋㅋ"))]))
        self.assertIsNone(self.reason([incoming()], stop_hook_active=True))

    def test_reminder_explains_chitchat_exit(self):
        reason = self.reason([incoming()]) or ""
        self.assertIn("잡담", reason)
        self.assertIn("두 번째", reason)

    def test_thread_reminder_requires_necessity_not_a_start_receipt(self):
        reason = self.reason([incoming()]) or ""
        for instruction in ("teammate asked", "Yoyoo instructed", "server answer",
                            "Do not post a receipt", "skip the comment",
                            "permission, not a requirement"):
            with self.subTest(instruction=instruction):
                self.assertIn(instruction, reason)
        self.assertNotIn("답하세요.", reason)
        self.assertNotIn("지금 알리세요.", reason)

    def test_other_pane_unchanged(self):
        self.panes("worker-terminal")
        self.assertIsNone(self.reason([incoming()]))

    def test_missing_main_identity_fails_open(self):
        (self.root / ".local/state/orchestrator/main").unlink()
        self.assertIsNone(self.reason([incoming()]))

    def test_unrelated_message_passes(self):
        self.assertIsNone(self.reason([incoming("일반 메시지")]))

    def test_previous_turn_call_is_not_repeated(self):
        self.assertIsNone(self.reason([incoming(), incoming("다음 작업")]))

    def test_actual_main_channel_post_passes(self):
        self.assertIsNone(self.reason([incoming(), call(), result()]))

    def test_post_attempt_without_result_returns_turn(self):
        self.assertIsNotNone(self.reason([incoming(), call()]))

    def test_failed_post_returns_turn(self):
        self.assertIsNotNone(self.reason([incoming(), call(), result("post failed", error=True)]))

    def test_scheduled_main_post_returns_turn(self):
        self.assertIsNotNone(self.reason([incoming(), call(), result("아침 8시에 올림\n")]))

    def test_scheduled_ledger_does_not_prove_reply(self):
        replied = self.root / ".local/state/orchestrator/replied"
        replied.mkdir()
        (replied / "1794000000.000100").write_text("1794000005.000000")
        self.assertIsNotNone(self.reason([incoming(), call(), result("아침 8시에 올림\n")]))

    def test_old_post_before_call_returns_turn(self):
        self.assertIsNotNone(self.reason([call(), result(), incoming()]))

    def thread_post(self, channel="CTEAM", thread="1794000000.000100"):
        return call(name="mcp__slack__slack_reply_to_thread", channel=channel, thread_ts=thread, text="Reply")

    def thread_result(self, **extra):
        return result(json.dumps({"ok": True, "channel": "CTEAM", "ts": "1794000002.000300",
                                  "message": {"thread_ts": "1794000000.000100"}, **extra}))

    def test_actual_thread_reply_passes(self):
        self.assertIsNone(self.reason([incoming(), self.thread_post(), self.thread_result()]))

    def test_other_thread_reply_returns_turn(self):
        self.assertIsNotNone(self.reason([incoming(), self.thread_post(thread="1794000009.000900"),
                                          self.thread_result(message={"thread_ts": "1794000009.000900"})]))

    def test_other_channel_reply_returns_turn(self):
        self.assertIsNotNone(self.reason([incoming(), self.thread_post(channel="COTHER"),
                                          self.thread_result(channel="COTHER")]))

    def test_scheduled_thread_reply_returns_turn(self):
        response = json.dumps({"ok": True, "channel": "CTEAM", "scheduled_message_id": "Q123", "post_at": 1794020000})
        self.assertIsNotNone(self.reason([incoming(), self.thread_post(), result(response)]))

    def test_main_helper_actual_thread_reply_passes(self):
        command = "python3 /skills/main-channel.py reply --channel CTEAM --thread 1794000000.000100 /tmp/reply.md"
        self.assertIsNone(self.reason([incoming(), call(command), result()]))

    def test_main_helper_scheduled_thread_reply_returns_turn(self):
        command = "python3 /skills/main-channel.py reply --channel CTEAM --thread 1794000000.000100 /tmp/reply.md"
        self.assertIsNotNone(self.reason([incoming(), call(command), result("아침 8시에 올림\n")]))

    def test_main_helper_other_thread_returns_turn(self):
        command = "python3 /skills/main-channel.py reply --channel CTEAM --thread 1794000009.000900 /tmp/reply.md"
        self.assertIsNotNone(self.reason([incoming(), call(command), result()]))

    def test_python_isolated_mode_post_passes(self):
        self.assertIsNone(self.reason([incoming(), call(POST.replace("python3 ", "python3 -I ")), result()]))

    def test_equal_sign_options_reply_passes(self):
        command = "python3 -I /skills/main-channel.py reply --channel=CTEAM --thread=1794000000.000100 /tmp/reply.md"
        self.assertIsNone(self.reason([incoming(), call(command), result()]))

    def test_post_followed_by_done_passes(self):
        command = POST + " && python3 /skills/main-channel.py done --thread 1794000000.000100"
        self.assertIsNone(self.reason([incoming(), call(command), result("1794000002.000300\nchecked: 1794000000.000100\n")]))

    def test_first_post_followed_by_exit_echo_passes(self):
        command = POST + '; echo "post exit $?"'
        self.assertIsNone(self.reason([incoming(), call(command), result("1794000002.000300\npost exit 0\n")]))

    def test_first_reply_followed_by_exit_echo_passes(self):
        command = "python3 /skills/main-channel.py reply --channel CTEAM --thread 1794000000.000100 /tmp/reply.md"
        self.assertIsNone(self.reason([incoming(), call(command + '; echo "post exit $?"'),
                                      result("1794000002.000300\npost exit 0\n")]))

    def test_exit_echo_requires_post_as_first_command(self):
        for prefix in ("false && ", "cd /tmp; ", 'echo "1794000002.000300"; '):
            with self.subTest(prefix=prefix):
                command = prefix + POST + '; echo "post exit $?"'
                self.assertIsNotNone(self.reason([incoming(), call(command), result("1794000002.000300\npost exit 0\n")]))

    def test_exit_echo_without_timestamp_returns_turn(self):
        command = POST + '; echo "post exit $?"'
        self.assertIsNotNone(self.reason([incoming(), call(command), result("아침 8시에 올림\npost exit 0\n")]))

    def test_exit_echo_does_not_allow_arbitrary_following_command(self):
        for suffix in ('; echo "1794000002.000300"', '; echo "post exit $?"; cat /tmp/ts'):
            with self.subTest(suffix=suffix):
                self.assertIsNotNone(self.reason([incoming(), call(POST + suffix), result()]))

    def test_exit_echo_does_not_allow_pipeline_or_background_command(self):
        for operator in ("|", "&"):
            with self.subTest(operator=operator):
                command = POST + " " + operator + ' cat /tmp/fake-ts; echo "post exit $?"'
                self.assertIsNotNone(self.reason([incoming(), call(command), result("1794000002.000300\npost exit 0\n")]))

    def test_nonzero_post_exit_returns_turn_even_with_timestamp(self):
        command = POST + '; echo "post exit $?"'
        self.assertIsNotNone(self.reason([incoming(), call(command), result("1794000002.000300\npost exit 1\n")]))

    def test_exit_echo_does_not_allow_process_or_command_substitution(self):
        for extra in ("> >(cat /tmp/fake-ts)", "2> >(cat /tmp/fake-ts)", "`cat /tmp/fake-ts`", "$(cat /tmp/fake-ts)",
                      '"$(cat /tmp/fake-ts >&2)"', '"`cat /tmp/fake-ts >&2`"'):
            with self.subTest(extra=extra):
                command = POST + " " + extra + '; echo "post exit $?"'
                self.assertIsNotNone(self.reason([incoming(), call(command), result("1794000002.000300\npost exit 0\n")]))

    def test_unexecuted_post_and_echo_returns_turn(self):
        self.assertIsNotNone(self.reason([incoming(), call("false && " + POST + "; echo 1794000002.000300"), result()]))

    def test_scheduled_post_and_echo_returns_turn(self):
        self.assertIsNotNone(self.reason([incoming(), call(POST + "; echo 1794000002.000300"),
                                          result("아침 8시에 올림\n1794000002.000300\n")]))

    def test_main_helper_heredoc_reply_passes(self):
        command = "python3 -I /skills/main-channel.py reply --channel CTEAM --thread 1794000000.000100 - <<'EOF'\nReply\nEOF"
        self.assertIsNone(self.reason([incoming(), call(command), result()]))

    def test_main_helper_heredoc_post_passes(self):
        command = "python3 /skills/main-channel.py post - --new <<'EOF'\nReply\nEOF"
        self.assertIsNone(self.reason([incoming(), call(command), result()]))

    def test_heredoc_cannot_hide_other_commands(self):
        commands = [
            "python3 /skills/main-channel.py post - <<'EOF'; cat /tmp/fake-ts\nReply\nEOF",
            "python3 /skills/main-channel.py post - <<EOF\n$(cat /tmp/fake-ts >&2)\nEOF",
        ]
        for command in commands:
            for suffix in ("", '\necho "post exit $?"'):
                with self.subTest(command=command, suffix=suffix):
                    self.assertIsNotNone(self.reason([incoming(), call(command + suffix), result()]))

    def test_fake_helper_in_heredoc_returns_turn(self):
        command = "cat <<'EOF'\n" + POST + "\nEOF"
        self.assertIsNotNone(self.reason([incoming(), call(command), result()]))

    def test_standard_ambient_reply_passes(self):
        command = "MUNICE_AMBIENT_THREAD=CTEAM/1794000000.000100 bash /skills/ambient/reply.sh /tmp/reply.md"
        self.assertIsNone(self.reason([incoming(), call(command), result()]))

    def test_standard_ambient_wrong_thread_returns_turn(self):
        command = "MUNICE_AMBIENT_THREAD=CTEAM/1794000009.000900 bash /skills/ambient/reply.sh /tmp/reply.md"
        self.assertIsNotNone(self.reason([incoming(), call(command), result()]))

    def main_channel_source(self):
        source = self.root / "work/workspace/.agents/skills/agent-board/ambient/collect.py"
        source.parent.mkdir(parents=True)
        source.write_text('raise RuntimeError("must never execute this source")\nMAIN_CHANNEL = "CMAIN"\n')

    def test_helper_reply_to_main_channel_passes(self):
        self.main_channel_source()
        command = "python3 -I /skills/main-channel.py reply --channel CMAIN --thread 1794000009.000900 /tmp/reply.md"
        self.assertIsNone(self.reason([incoming(), call(command), result()]))

    def test_direct_api_reply_to_main_channel_passes(self):
        self.main_channel_source()
        self.assertIsNone(self.reason([incoming(), self.thread_post(channel="CMAIN"), self.thread_result(channel="CMAIN")]))

    def test_scheduled_main_channel_api_returns_turn(self):
        self.main_channel_source()
        response = json.dumps({"ok": True, "channel": "CMAIN", "scheduled_message_id": "Q123"})
        self.assertIsNotNone(self.reason([incoming(), self.thread_post(channel="CMAIN"), result(response)]))

    def test_echo_of_post_is_not_a_reply(self):
        self.assertIsNotNone(self.reason([incoming(), call("echo '" + POST + "'"), result()]))

    def test_scheduling_tool_with_timestamp_is_not_a_reply(self):
        self.assertIsNotNone(self.reason([incoming(), call(name="mcp__slack__schedule_message", channel="CTEAM",
                                                          thread_ts="1794000000.000100"), self.thread_result()]))

    def test_slack_api_failure_returns_turn(self):
        self.assertIsNotNone(self.reason([incoming(), self.thread_post(), result('{"ok":false,"error":"denied"}')]))

    def test_wrong_result_id_returns_turn(self):
        self.assertIsNotNone(self.reason([incoming(), call(), result(ident="other")]))

    def test_reaction_call_without_yoyoo_words_returns_turn(self):
        self.assertIsNotNone(self.reason([incoming(CALL.split(" 요유 글:")[0] + " 요유 글 없음")]))

    def test_codex_unanswered_returns_turn(self):
        entry = {"type": "response_item", "payload": {"type": "message", "role": "user",
                 "content": [{"type": "input_text", "text": CALL}]}}
        self.assertIsNotNone(self.reason([entry]))

    def test_codex_actual_post_passes(self):
        command = {"type": "response_item", "payload": {"type": "custom_tool_call", "name": "exec", "call_id": "p",
                   "input": "text(await tools.exec_command({cmd:" + json.dumps(POST) + "}))"}}
        output = {"type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": "p",
                  "output": json.dumps({"output": "1794000002.000300\n", "exit_code": 0})}}
        self.assertIsNone(self.reason([incoming(), command, output]))

    def test_codex_scheduled_post_returns_turn(self):
        command = {"type": "response_item", "payload": {"type": "function_call", "name": "exec_command", "call_id": "p",
                   "arguments": json.dumps({"cmd": POST})}}
        output = {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "p",
                  "output": json.dumps({"output": "아침 8시에 올림\n", "exit_code": 0})}}
        self.assertIsNotNone(self.reason([incoming(), command, output]))

    def test_hook_cli_returns_feedback_in_both_harnesses(self):
        self.reason([incoming()])
        payload = {"session_id": "session", "transcript_path": str(self.root / "session.jsonl"),
                   "last_assistant_message": "", "stop_hook_active": False}
        script = Path(__file__).with_name("yoyoo-share-guard.py")
        for flags in ([], ["--claude"]):
            with self.subTest(flags=flags):
                run = subprocess.run([sys.executable, "-B", str(script), *flags], input=json.dumps(payload),
                                     capture_output=True, text=True, env=self.env, check=True)
                output = json.loads(run.stdout)
                if flags:
                    self.assertEqual(output["hookSpecificOutput"]["hookEventName"], "Stop")
                    self.assertIn("잡담", output["hookSpecificOutput"]["additionalContext"])
                else:
                    self.assertEqual(output["decision"], "block")
                payload["stop_hook_active"] = True
                second = subprocess.run([sys.executable, "-B", str(script), *flags], input=json.dumps(payload),
                                        capture_output=True, text=True, env=self.env, check=True)
                self.assertEqual(second.stdout, "")
                payload["stop_hook_active"] = False

    def test_unreadable_transcript_fails_open(self):
        self.assertIsNone(guard.decide({"transcript_path": str(self.root / "missing"),
                                        "last_assistant_message": ""}, self.env))

    def test_malformed_transcript_fails_open(self):
        self.assertIsNone(self.reason([{"unknown": True}]))


if __name__ == "__main__":
    unittest.main()
