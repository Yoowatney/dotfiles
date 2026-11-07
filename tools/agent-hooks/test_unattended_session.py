#!/usr/bin/env python3
"""Offline unattended-session checks using both engines' transcript envelopes."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("job_host", HERE / "job-host-guard.py")
job_host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(job_host)


def user(engine, text):
    if engine == "claude":
        return {"type": "user", "origin": {"kind": "human"}, "message": {"content": text}}
    return {"type": "response_item", "payload": {"type": "message", "role": "user",
            "content": [{"type": "input_text", "text": text}]}}


class UnattendedSessionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "session.jsonl"

    def active(self, engine, mode, *texts, extra=()):
        records = [user(engine, text) for text in texts] + list(extra)
        self.path.write_text("".join(json.dumps(x) + "\n" for x in records))
        env = {"MUNICE_AMBIENT" if mode == "ambient" else "MUNICE_AUTO_DISPATCH": "1"}
        return job_host.job_tab({"transcript_path": str(self.path)}, env)

    def test_dispatch_takeover_persists_and_ambient_relocks(self):
        for engine in ("claude", "codex"):
            with self.subTest(engine=engine):
                self.assertTrue(self.active(engine, "dispatch", "[auto-dispatch] task"))
                self.assertFalse(self.active(engine, "dispatch", "[auto-dispatch] task", "직접 입력"))
                self.assertFalse(self.active(engine, "dispatch", "직접 입력", "[auto-dispatch] next"))
                self.assertTrue(self.active(engine, "ambient", "[ambient-round] task"))
                self.assertFalse(self.active(engine, "ambient", "[ambient-round] task", "직접 입력"))
                self.assertTrue(self.active(engine, "ambient", "[ambient-round] task", "직접 입력", "[ambient-round] next"))

    def test_injected_context_and_non_user_records_never_lift(self):
        for text in ("# AGENTS.md instructions for /repo", "<environment_context>context</environment_context>",
                     "<user_instructions>rules</user_instructions>", "<hook_prompt>hook</hook_prompt>",
                     "<user_action>action</user_action>"):
            with self.subTest(text=text):
                self.assertTrue(self.active("codex", "dispatch", text, "[auto-dispatch] task"))
        records = [
            {"type": "response_item", "payload": {"role": "assistant", "content": [{"text": "직접 입력"}]}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": "duplicate"}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "content": "직접 입력"}]}}
        ]
        self.assertTrue(self.active("codex", "ambient", "[ambient-round] task", extra=records))
        self.assertTrue(self.active("claude", "ambient", "[ambient-round] task", extra=records))

    def test_missing_corrupt_or_empty_records_keep_guard(self):
        env = {"MUNICE_AUTO_DISPATCH": "1"}
        self.assertTrue(job_host.job_tab({"transcript_path": str(self.path)}, env))
        self.path.write_text('bad json\n[]\n{"type":"response_item","payload":null}\n')
        self.assertTrue(job_host.job_tab({"transcript_path": str(self.path)}, env))
        self.assertFalse(job_host.job_tab({"transcript_path": str(self.path)}, {}))

    def test_ambient_wins_when_both_flags_are_set(self):
        self.active("codex", "ambient", "[auto-dispatch] task", "direct", "[ambient-round] task")
        self.assertTrue(job_host.job_tab({"transcript_path": str(self.path)},
                                         {"MUNICE_AMBIENT": "1", "MUNICE_AUTO_DISPATCH": "1"}))

    def test_claude_queued_round_relocks_after_takeover(self):
        queued = {"type": "attachment", "attachment": {"type": "queued_command",
                  "prompt": "[ambient-round] next", "commandMode": "prompt",
                  "origin": {"kind": "human"}, "humanTurn": True}}
        self.assertTrue(self.active("claude", "ambient", "[ambient-round] first", "direct", extra=[queued]))
        queued["attachment"]["prompt"] = "direct queued instruction"
        self.assertFalse(self.active("claude", "dispatch", "[auto-dispatch] first", extra=[queued]))

    def test_reader_load_failure_keeps_job_guard(self):
        helper = Path(self.tmp.name) / "unattended-session.py"
        helper.write_text('raise RuntimeError("offline reader failure")\n')
        with mock.patch.object(job_host, "__file__", str(helper.with_name("job-host-guard.py"))):
            self.assertTrue(job_host.job_tab({"transcript_path": str(self.path)}, {"MUNICE_AUTO_DISPATCH": "1"}))

    def test_repeated_job_install_stops_until_human_takes_over(self):
        for engine in ("claude", "codex"):
            with self.subTest(engine=engine):
                env = {"MUNICE_AUTO_DISPATCH": "1", "JOB_HOST_DIR": self.tmp.name,
                       "JOB_HOST_MODEL": job_host.LAPTOP_MODEL}
                payload = {"session_id": engine, "transcript_path": str(self.path), "tool_name": "Bash",
                           "tool_input": {"command": "launchctl load /tmp/offline-test.plist"}}
                self.active(engine, "dispatch", "[auto-dispatch] task")
                for _ in range(2):
                    self.assertEqual(job_host.check(payload, env), job_host.JOB_ASK)
                self.active(engine, "dispatch", "[auto-dispatch] task", "이 잡은 회사맥에 올려")
                self.assertEqual(job_host.check(payload, env), job_host.ASK)
                self.assertIsNone(job_host.check(payload, env))
                payload["tool_input"]["command"] = "launchctl list"
                self.assertIsNone(job_host.check(payload, env))


if __name__ == "__main__":
    unittest.main(verbosity=2)
