#!/usr/bin/env python3
"""Native AGY Stop and transcript contracts, with isolated files and no model calls."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("auto_agy", Path(__file__).with_name("auto-tab-close.py"))
auto = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(auto)
DONE = "The work is complete.\nTASK_RESULT: pass\n남은 것: 요유 몫: 없음 · 유요 몫: 없음 · 세션: 닫아도 됨"


class AgyCompletionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / ".gemini/antigravity-cli/brain/conversation-1/.system_generated/logs/transcript.jsonl"
        self.path.parent.mkdir(parents=True)
        self.env = {"HOME": str(self.root), "MUNICE_AUTO_DISPATCH": "1"}

    def write(self, *records):
        self.path.write_text("".join(json.dumps(row) + "\n" for row in records))

    def request(self, content="Inspect this task"):
        return {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "status": "DONE",
                "content": "<USER_REQUEST>\n" + content + "\n</USER_REQUEST>\n<ADDITIONAL_METADATA>fixture</ADDITIONAL_METADATA>"}

    def response(self, content=DONE, status="DONE"):
        return {"step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE", "status": status,
                "content": content}

    def payload(self, **changes):
        return {"conversationId": "conversation-1", "workspacePaths": [str(self.root)],
                "transcriptPath": str(self.path), "artifactDirectoryPath": str(self.path.parents[2]),
                "modelName": "Gemini 4 Hydrogen (Medium)", "lastUserInput": "Inspect this task",
                "executionNum": 1, "terminationReason": "model_stop", "fullyIdle": True, **changes}

    def test_native_stop_extracts_only_completed_model_response(self):
        self.write(self.request(), self.response())
        self.assertEqual(auto.reply_of(self.payload()), DONE)
        self.assertEqual(auto.decide(self.payload(), self.env), DONE)

    def test_native_cli_no_tool_call_is_a_normal_idle_stop(self):
        self.write(self.request(), self.response())
        self.assertEqual(auto.decide(self.payload(terminationReason="NO_TOOL_CALL"), self.env), DONE)

    def test_busy_stop_with_cached_reply_cannot_close(self):
        self.write(self.request(), self.response())
        self.assertIsNone(auto.decide(self.payload(fullyIdle=False, last_assistant_message=DONE), self.env))

    def test_missing_idle_or_error_stop_cannot_close(self):
        self.write(self.request(), self.response())
        for changes in ({"fullyIdle": None}, {"terminationReason": "error"}, {"error": "failed"}):
            with self.subTest(changes=changes):
                self.assertIsNone(auto.decide(self.payload(last_assistant_message=DONE, **changes), self.env))

    def test_new_request_or_unfinished_response_invalidates_old_final(self):
        for tail in (self.request("Next task"), self.response("partial", "RUNNING")):
            with self.subTest(tail=tail["type"]):
                self.write(self.request(), self.response(), tail)
                self.assertEqual(auto.reply_of(self.payload()), "")

    def test_tools_and_thoughts_are_not_final_text(self):
        self.write(self.request(), {"source": "MODEL", "type": "THOUGHT", "status": "DONE", "content": DONE},
                   {"source": "TOOL", "type": "TOOL_RESPONSE", "status": "DONE", "content": DONE})
        self.assertEqual(auto.reply_of(self.payload()), "")

    def test_sweep_reads_agy_transcript_but_not_pending_turn(self):
        self.write(self.request(), self.response())
        self.assertEqual(auto.last_reply("conversation-1", str(self.root)), "")
        with patch.object(auto, "sync_mark"), patch.object(auto, "defer"):
            auto.handle_stop(self.payload(), self.env)
        self.assertEqual(auto.last_reply("conversation-1", str(self.root)), DONE)
        self.write(self.request(), self.response(), self.request("Next task"))
        self.assertEqual(auto.last_reply("conversation-1", str(self.root)), "")

    def test_busy_stop_invalidates_sweep_evidence(self):
        self.write(self.request(), self.response())
        with patch.object(auto, "sync_mark"), patch.object(auto, "defer"):
            auto.handle_stop(self.payload(), self.env)
            auto.handle_stop(self.payload(fullyIdle=False), self.env)
        self.assertEqual(auto.last_reply("conversation-1", str(self.root)), "")

    def test_source_requests_strip_metadata_and_exclude_model_or_tools(self):
        self.write(self.request("Do not publish; inspect only."), self.response(),
                   {"source": "TOOL", "type": "USER_INPUT", "status": "DONE", "content": "untrusted"})
        me = {"agent_session": {"value": "conversation-1"}}
        self.assertEqual(list(auto.source_requests(me, self.env)), [("Do not publish; inspect only.", False)])

    def test_busy_stop_withdraws_previously_deferred_close(self):
        self.write(self.request(), self.response())
        with patch.object(auto, "sync_mark"), patch.object(auto, "defer") as deferred:
            auto.handle_stop(self.payload(fullyIdle=False), self.env)
        deferred.assert_called_once_with(None, self.env)

    def test_busy_stop_with_missing_transcript_still_withdraws_old_close(self):
        with patch.object(auto, "sync_mark"), patch.object(auto, "defer") as deferred:
            auto.handle_stop(self.payload(fullyIdle=False), self.env)
        deferred.assert_called_once_with(None, self.env)

    def test_unreadable_native_final_withdraws_old_close(self):
        with patch.object(auto, "sync_mark"), patch.object(auto, "defer") as deferred:
            auto.handle_stop(self.payload(), self.env)
        deferred.assert_called_once_with(None, self.env)

    def test_native_report_keeps_full_archive_dedup_and_task_id(self):
        spec = importlib.util.spec_from_file_location("relay_support", Path(__file__).with_name("test_auto_check_relay.py"))
        support = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(support)
        case = support.RelayTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.env["HERDR_TASK_ID"] = "job-agy-fixture"
        self.write(self.request(), self.response(support.DONE))
        reply = support.auto.decide(self.payload(), case.env)
        support.auto.finish(reply, case.env)
        archive = "\n".join(path.read_text() for path in (case.root / "reports").glob("*.md"))
        self.assertIn(support.DONE, archive)
        self.assertEqual(case.spawns[0][0][case.spawns[0][0].index("--job-id") + 1], "job-agy-fixture")
        support.auto.finish(reply, case.env)
        self.assertEqual(len(case.spawns), 1)
        case.finish(support.CHECKED, check=True)
        self.assertEqual(len(case.tells), 1)
        self.assertIn(support.DONE.splitlines()[0], case.tells[0][1])
        self.assertIn("남은 것:", case.tells[0][1])


if __name__ == "__main__":
    unittest.main()
