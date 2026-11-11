"""Exercise notification meaning and delivery. Run directly or with --selftest (pre-push)."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest


def load_scan():
    spec = importlib.util.spec_from_file_location("transcript_scan", Path(__file__).with_name("transcript-secret-scan.py"))
    scan = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scan)
    return scan


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.scan = load_scan()
        self.scan.ROOTS = ["/sessions"]

    def test_masked_report_explains_action_and_limits_without_exposing_paths(self):
        text = self.scan.report({"/sessions/private-name.jsonl": {"github_token": 2}}, 5, False)
        first = text.splitlines()[0]
        self.assertIn("2곳", first)
        self.assertIn("요유 할 일:", first)
        self.assertIn("실제 사용 중인 비밀값", first)
        self.assertIn("재발급", first)
        self.assertIn("*", text)
        self.assertIn("세션 기록", text)
        self.assertIn("폐기하거나 재발급하지", text)
        self.assertIn("새 토큰", text)
        self.assertIn("중복", text)
        self.assertIn("시험용", text)
        self.assertNotIn("private-name", text)
        self.assertIn("github_token: 2곳", text)
        self.assertIn("파일 5개 중 1개", text)

    def test_dry_report_explains_action_without_claiming_masking(self):
        text = self.scan.report({"/sessions/a.jsonl": {"slack_token": 1}}, 1, True)
        self.assertIn("가리지 않음", text.splitlines()[0])
        self.assertIn("실제 사용 중인 비밀값으로 확인되면", text.splitlines()[0])
        self.assertNotIn("덮었습니다", text)

    def test_reappearing_token_still_sends_a_second_dm(self):
        # Wording changes must not silently introduce deduplication or suppress notifications.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sessions"
            source.mkdir()
            sent = root / "sent.jsonl"
            transport = root / "slack.py"
            transport.write_text(
                "import json\nclass Slack:\n"
                "    def post(self, channel, thread, text):\n"
                f"        with open({str(sent)!r}, 'a') as f:\n"
                "            f.write(json.dumps(text) + '\\n')\n"
            )
            self.scan.ROOTS = [str(source)]
            self.scan.STATE = str(root / "state" / "state.json")
            self.scan.ENV_DIR = str(root / "no-config")
            self.scan.PUBLIC_KEY_REPOS = []
            self.scan.SLACK_PY = str(transport)
            token = "ghp_" + "aB3dE5fG7hJ9kL1mN2pQ4rS6tU8vW0xY2zA4"
            with contextlib.redirect_stdout(io.StringIO()):
                for name in ("a.jsonl", "copy.jsonl"):
                    path = source / name
                    path.write_text(json.dumps({"content": token}) + "\n")
                    self.scan.run([])
                    self.assertNotIn(token, path.read_text())
            messages = [json.loads(line) for line in sent.read_text().splitlines()]
            self.assertEqual(len(messages), 2)
            self.assertTrue(all(token not in text for text in messages))


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]] if sys.argv[1:] == ["--selftest"] else None)
