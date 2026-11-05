"""Exercise real hook subprocess output without invoking external services."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SUBJECT = Path(__file__).with_name("session-context-limit.py")


class ContextLimitTest(unittest.TestCase):
    def run_hook(self, directory, context, limit=2000):
        output = {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context}, "continue": True}
        command = [sys.executable, str(SUBJECT), "--label", "board", "--limit", str(limit), "--", sys.executable, "-c", "import sys; sys.stdin.read(); print(" + repr(json.dumps(output)) + ")"]
        return subprocess.run(command, input='{"session_id":"test"}', text=True, capture_output=True, env={**os.environ, "XDG_STATE_HOME": directory})

    def test_long_context_is_private_complete_file_with_mandatory_read_pointer(self):
        with tempfile.TemporaryDirectory() as directory:
            original = "Board rules: read /private/rules\n" + "x" * 10000 + "TAIL"
            result = self.run_hook(directory, original)
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(result.stdout)
            context = output["hookSpecificOutput"]["additionalContext"]
            files = list(Path(directory).rglob("*.txt"))
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].read_text(), original)
            self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)
            self.assertIn(str(files[0]), context)
            self.assertIn("Read it before selecting or updating work", context)
            self.assertLess(len(context), 1000)
            self.assertTrue(output["continue"])

    def test_short_context_stays_identical(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_hook(directory, "short")
            self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"], "short")
            self.assertEqual(list(Path(directory).rglob("*.txt")), [])

    def test_non_json_and_failure_are_not_hidden(self):
        result = subprocess.run([sys.executable, str(SUBJECT), "--label", "board", "--", sys.executable, "-c", "import sys; print('plain'); sys.exit(7)"], input="{}", text=True, capture_output=True)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, "plain\n")


if __name__ == "__main__":
    unittest.main()
