#!/usr/bin/env python3
"""Local, isolated startup recovery tests; never opens a real pane."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
import unittest

spec = importlib.util.spec_from_file_location("check_start", Path(__file__).with_name("automatic-check-start.py"))
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)


class Startup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.launcher = self.root / "launcher"
        self.context = {}

    def run_start(self, source, timeout=12):
        self.launcher.write_text("#!/bin/bash\n" + source)
        self.launcher.chmod(0o700)
        return M.start([str(self.launcher)], dict(os.environ, TEST_ROOT=str(self.root)),
                       str(self.root / "receipt"), self.context, lambda: None, attempt_timeout=timeout)

    def test_start_failure_retries_once_then_succeeds(self):
        address, error = self.run_start('''
if [ ! -f "$TEST_ROOT/once" ]; then touch "$TEST_ROOT/once"; echo 'agent_not_ready' >&2; exit 1; fi
echo workers/w2:p3
''')
        self.assertEqual((address, error), ("workers/w2:p3", ""))
        self.assertEqual(len(self.context["start_attempts"]), 2)

    def test_quota_failure_is_bounded_and_not_repeated_by_next_stop(self):
        source = "echo 'quota limit reached' >&2; exit 1\n"
        address, error = self.run_start(source)
        self.assertFalse(address)
        self.assertIn("quota limit reached", error)
        self.assertEqual(len(self.context["start_attempts"]), 2)
        self.run_start(source)
        self.assertEqual(len(self.context["start_attempts"]), 2)

    def test_readiness_timeout_cancels_before_retry_within_two_minutes(self):
        started = time.monotonic()
        address, error = self.run_start('''
trap 'echo "{\\"status\\":\\"closed\\"}" > "$PANE_SPAWN_CHECK_RECEIPT"; exit 124' TERM
echo '{"status":"creating"}' > "$PANE_SPAWN_CHECK_RECEIPT"
sleep 10 & wait
''', timeout=1)
        self.assertFalse(address)
        self.assertIn("timed out", error)
        self.assertEqual(len(self.context["start_attempts"]), 2)
        self.assertLess(time.monotonic() - started, 5)

    def test_uncertain_open_is_not_duplicated(self):
        address, error = self.run_start('''
echo '{"status":"creating"}' > "$PANE_SPAWN_CHECK_RECEIPT"
exit 1
''')
        self.assertFalse(address)
        self.assertIn("cleanup unconfirmed", error)
        self.assertEqual(len(self.context["start_attempts"]), 1)

    def test_uncertain_open_after_slow_startup_is_not_duplicated(self):
        address, error = self.run_start('''
sleep 2.2
echo '{"status":"creating"}' > "$PANE_SPAWN_CHECK_RECEIPT"
exit 1
''')
        self.assertFalse(address)
        self.assertIn("cleanup unconfirmed", error)
        self.assertEqual(len(self.context["start_attempts"]), 1)

    def test_interrupted_attempt_is_not_replayed(self):
        self.context["start_attempts"] = [{"started_at": time.time()}]
        address, error = self.run_start("echo workers/w2:p3\n")
        self.assertFalse(address)
        self.assertIn("interrupted", error)

    def test_success_receipt_survives_interrupted_outer_context_save(self):
        self.context["start_attempts"] = [{"address": "workers/w2:p3"}]
        self.assertEqual(self.run_start("exit 1\n"), ("workers/w2:p3", ""))

    def test_uncertain_cleanup_is_not_retried_by_next_stop(self):
        source = 'echo \'{"status":"creating"}\' > "$PANE_SPAWN_CHECK_RECEIPT"; exit 1\n'
        self.run_start(source)
        self.run_start(source)
        self.assertEqual(len(self.context["start_attempts"]), 1)


if __name__ == "__main__":
    unittest.main()
