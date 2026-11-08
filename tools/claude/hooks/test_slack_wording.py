"""Yoyoo's Slack wording uses the shared main-channel validator."""
import importlib.util
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("slack_format", Path(__file__).with_name("slack-format-check.py"))
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)


class WordingTests(unittest.TestCase):
    def setUp(self):
        self.collect = SimpleNamespace(MAIN_CHANNEL="C_MAIN_FIXTURE", YOYOO="U_YOYOO_FIXTURE", YOYOO_DM="D_YOYOO_FIXTURE")
        self.words = SimpleNamespace(PANE_WORD=re.compile("fixture-pane-word"))

    def check(self, inp):
        with mock.patch.object(hook, "wording_modules", return_value=(self.collect, self.words)):
            return hook.yoyoo_wording_problems(inp)

    def test_channel_and_user_targets_use_shared_word_detector(self):
        for field in ("channel", "channel_id"):
            for target in (self.collect.MAIN_CHANNEL, self.collect.YOYOO, self.collect.YOYOO_DM):
                for body in ("text", "message"):
                    with self.subTest(field=field, target=target, body=body):
                        self.assertTrue(self.check({field: target, body: "fixture-pane-word"}))
                        self.assertFalse(self.check({field: target, body: "세션"}))

    def test_other_channels_are_not_yoyoo_targets(self):
        self.assertFalse(self.check({"channel_id": "C_OTHER", "text": "fixture-pane-word"}))

    def test_both_tool_payload_shapes_run_the_wording_guard(self):
        for tool, body in (("mcp__slack__slack_post_message", "text"),
                           ("mcp__codex_apps__slack_slack_send_message", "message")):
            payload = {"hook_event_name": "PreToolUse", "tool_name": tool,
                       "tool_input": {"channel_id": self.collect.MAIN_CHANNEL, body: "fixture-pane-word"}}
            with self.subTest(tool=tool), mock.patch.object(hook, "wording_modules",
                    return_value=(self.collect, self.words)):
                self.assertIn("세션", hook.check(payload))

    def test_standard_markdown_message_keeps_its_existing_format_behavior(self):
        payload = {"tool_name": "mcp__codex_apps__slack_slack_send_message",
                   "tool_input": {"channel_id": self.collect.MAIN_CHANNEL, "message": "**세션**"}}
        with mock.patch.object(hook, "wording_modules", return_value=(self.collect, self.words)), \
                mock.patch.object(hook, "fresh_reason", return_value=None):
            self.assertIsNone(hook.check(payload))

    def test_nested_edit_message_text_and_blocks_are_checked(self):
        for message in ({"markdown_text": "fixture-pane-word"}, {"text": "fixture-pane-word"},
                        {"text": "세션", "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "fixture-pane-word"}}]},
                        {"blocks": [{"type": "rich_text", "elements": [{"type": "rich_text_section", "elements":
                            [{"type": "text", "text": "fixture-pane-word"}]}]}]}):
            payload = {"tool_name": "mcp__codex_apps__slack_slack_edit_message", "tool_input": {
                "channel_id": self.collect.MAIN_CHANNEL, "message_id": "fixture", "message": message}}
            with self.subTest(message=message), mock.patch.object(hook, "wording_modules",
                    return_value=(self.collect, self.words)):
                self.assertIn("세션", hook.check(payload))

    def test_missing_shared_checker_refuses_known_targets(self):
        with mock.patch.object(hook, "wording_modules", side_effect=OSError("fixture")):
            self.assertTrue(hook.yoyoo_wording_problems({"channel_id": "C_MAIN_FIXTURE", "text": "text"}))

    def test_missing_detector_only_refuses_yoyoo_targets(self):
        with tempfile.TemporaryDirectory() as folder:
            ambient = Path(folder) / "ambient"
            ambient.mkdir()
            (ambient / "collect.py").write_text('MAIN_CHANNEL="C_MAIN_FIXTURE"\nYOYOO="U_YOYOO_FIXTURE"\nYOYOO_DM="D_YOYOO_FIXTURE"\n')
            with mock.patch.object(hook, "AGENT_BOARD", folder):
                self.assertFalse(hook.yoyoo_wording_problems({"channel_id": "C_OTHER", "text": "text"}))
                self.assertTrue(hook.yoyoo_wording_problems({"channel_id": "C_MAIN_FIXTURE", "text": "text"}))
                self.assertTrue(hook.yoyoo_wording_problems({"channel_id": "D_YOYOO_FIXTURE", "text": "text"}))

    def test_a_stale_or_broken_shared_checker_refuses_instead_of_crashing(self):
        # A crash lets the call through with every later check skipped, the tag of a teammate who left included.
        for label, collect, words, target in (
                ("collect without YOYOO_DM", 'MAIN_CHANNEL="C_MAIN_FIXTURE"\nYOYOO="U_YOYOO_FIXTURE"\n', 'import re\nPANE_WORD=re.compile("x")\n', "C_OTHER"),
                ("collect fails on import", 'raise RuntimeError("fixture")\n', 'import re\nPANE_WORD=re.compile("x")\n', "C_OTHER"),
                ("collect does not parse", 'MAIN_CHANNEL = (\n', 'import re\nPANE_WORD=re.compile("x")\n', "C_OTHER"),
                ("detector without PANE_WORD", 'MAIN_CHANNEL="C_MAIN_FIXTURE"\nYOYOO="U_YOYOO_FIXTURE"\nYOYOO_DM="D_YOYOO_FIXTURE"\n', 'X = 1\n', "C_MAIN_FIXTURE")):
            with self.subTest(label), tempfile.TemporaryDirectory() as folder:
                for name, body in (("ambient/collect.py", collect), ("main/pane_wording.py", words)):
                    (Path(folder) / name).parent.mkdir(exist_ok=True)
                    (Path(folder) / name).write_text(body)
                payload = {"tool_name": "mcp__slack__slack_post_message", "tool_input": {"channel_id": target, "text": "<@U0LEFT>"}}
                with mock.patch.object(hook, "AGENT_BOARD", folder), mock.patch.object(hook, "fresh_reason", return_value=None):
                    self.assertIn("용어 검사를 읽지 못했습니다", hook.check(payload) or "")


if __name__ == "__main__":
    unittest.main()
