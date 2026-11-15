#!/usr/bin/env python3
"""Exercise real child commands, persistent streaks and the existing inbox."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

RUNNER = Path(__file__).with_name('scheduled-job-health.py')


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = {**os.environ, 'HOME': str(self.root), 'XDG_STATE_HOME': str(self.root / 'state')}
        self.spec = self.root / 'job.json'

    def run_job(self, code=0, label='example.hourly', script=None, logs=None):
        self.spec.write_text(json.dumps({'label': label, 'command': [sys.executable, '-c',
            script or f'import sys; sys.exit({code})'], 'logs': logs or []}))
        return subprocess.run([sys.executable, str(RUNNER), 'run', '--spec', str(self.spec)],
                              env=self.env, capture_output=True, text=True)

    def events(self):
        path = self.root / 'state/orchestrator/alerts/state.json'
        return json.loads(path.read_text())['events'] if path.exists() else {}

    def test_single_failure_is_unchanged_and_second_alerts_once(self):
        self.assertEqual(self.run_job(7).returncode, 7)
        self.assertEqual(len(self.events()), 0)
        self.assertEqual(self.run_job(9).returncode, 9)
        self.assertEqual(len(self.events()), 1)
        self.run_job(9)
        self.run_job(9)
        self.assertEqual(len(self.events()), 1)

    def test_success_resets_streak_and_allows_a_new_episode(self):
        for code in [1, 0, 1]:
            self.run_job(code)
        self.assertEqual(len(self.events()), 0)
        for code in [1, 0, 1, 1]:
            self.run_job(code)
        self.assertEqual(len(self.events()), 2)

    def test_distinct_jobs_never_share_a_streak(self):
        self.run_job(1, 'first')
        self.run_job(1, 'second')
        self.assertEqual(len(self.events()), 0)

    def test_failure_followed_by_explicit_skip_alerts_without_changing_exit(self):
        self.run_job(1)
        result = self.run_job(script="import os,pathlib; pathlib.Path(os.environ['SCHEDULED_JOB_OUTCOME_FILE']).write_text('skipped')")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.events()), 1)

    def test_only_new_whole_job_skip_lines_count(self):
        log = self.root / 'run.log'
        log.write_text('offline, skipped\n')
        logs = [{'path': str(log), 'skip_pattern': r'^offline, skipped$'}]
        self.run_job(logs=logs)
        self.assertEqual(len(self.events()), 0)
        script = f"from pathlib import Path; p=Path({str(log)!r}); p.open('a').write('offline, skipped\\n')"
        self.run_job(script=script, logs=logs)
        self.assertEqual(len(self.events()), 0)
        self.run_job(script=script, logs=logs)
        self.assertEqual(len(self.events()), 1)

    def test_partial_skip_followed_by_success_is_success(self):
        log = self.root / 'run.log'
        logs = [{'path': str(log), 'skip_pattern': '^skip:', 'success_pattern': '^ok$'}]
        self.run_job(1)
        self.run_job(script=f"from pathlib import Path; Path({str(log)!r}).write_text('skip: optional step\\nok\\n')", logs=logs)
        self.assertEqual(len(self.events()), 0)

    def test_inbox_contention_retries_before_next_command_without_duplicate(self):
        alerts = self.root / 'state/orchestrator/alerts'
        alerts.mkdir(parents=True)
        with (alerts / 'lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.run_job(1)
            self.run_job(1)
            self.assertEqual(len(self.events()), 0)
        inbox = alerts / 'state.json'
        script = f"import json; assert len(json.load(open({str(inbox)!r}))['events']) == 1"
        result = self.run_job(script=script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.events()), 1)

    def test_failure_logged_with_exit_zero_still_counts(self):
        log = self.root / 'run.log'
        logs = [{'path': str(log), 'failure_pattern': 'update failed:'}]
        script = f"from pathlib import Path; Path({str(log)!r}).open('a').write('update failed: fixture\\n')"
        self.run_job(script=script, logs=logs)
        self.run_job(script=script, logs=logs)
        self.assertEqual(len(self.events()), 1)

    def test_truncated_same_inode_log_does_not_hide_new_skip(self):
        log = self.root / 'run.log'
        log.write_text('ordinary old text\n')
        logs = [{'path': str(log), 'skip_pattern': '^offline, skipped$'}]
        script = f"from pathlib import Path; Path({str(log)!r}).write_text('offline, skipped\\npadding padding padding\\n')"
        self.run_job(1)
        self.run_job(script=script, logs=logs)
        self.assertEqual(len(self.events()), 1)

    def test_real_main_refresh_reports_missing_main_as_skipped(self):
        source = RUNNER.with_name('main-refresh')
        script = f"import subprocess; subprocess.check_call(['/bin/bash', {str(source)!r}])"
        self.run_job(script=script)
        self.run_job(script=script)
        self.assertEqual(len(self.events()), 1)

    def test_alert_does_not_copy_job_output_or_command_secrets(self):
        script = "import sys; print('SENSITIVE_VALUE'); sys.exit(2)"
        self.run_job(script=script)
        self.run_job(script=script)
        self.assertNotIn('SENSITIVE_VALUE', json.dumps(self.events()))

    def test_crash_after_enqueue_retries_the_same_immutable_event(self):
        alerts = self.root / 'state/orchestrator/alerts'
        alerts.mkdir(parents=True)
        with (alerts / 'lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.run_job(1)
            self.run_job(1)
        state = next((self.root / 'state/scheduled-job-health/runs').glob('*/state.json'))
        alert = json.loads(state.read_text())['outbox'][0]
        import importlib.util
        spec = importlib.util.spec_from_file_location('real_inbox', RUNNER.with_name('main-alert-inbox.py'))
        inbox = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(inbox)
        inbox.enqueue(self.env, **alert)
        self.run_job(1)
        self.assertEqual(len(self.events()), 1)
        self.assertEqual(json.loads(state.read_text())['outbox'], [])

    def test_unreadable_monitoring_state_runs_original_exactly_once(self):
        self.run_job()
        state = next((self.root / 'state/scheduled-job-health/runs').glob('*/state.json'))
        state.write_text('not valid json')
        calls = self.root / 'calls'
        result = self.run_job(script=f"from pathlib import Path; import sys; Path({str(calls)!r}).open('a').write('once\\n'); sys.exit(6)")
        self.assertEqual(result.returncode, 6)
        self.assertEqual(calls.read_text(), 'once\n')
        self.assertEqual(state.read_text(), 'not valid json')

    def test_valid_json_with_wrong_schema_does_not_block_original_command(self):
        self.run_job()
        state = next((self.root / 'state/scheduled-job-health/runs').glob('*/state.json'))
        for bad in ['[]', '{"version": 1}', '{"version": 1, "streak": 1, "episode": null, "active": null, "recent": [], "outbox": []}']:
            with self.subTest(state=bad):
                state.write_text(bad)
                self.assertEqual(self.run_job(8).returncode, 8)
                self.assertEqual(state.read_text(), bad)

    def test_runner_killed_between_attempts_counts_incomplete_attempt_once(self):
        self.spec.write_text(json.dumps({'label': 'interrupted', 'command': [sys.executable, '-c',
            f"import pathlib,time; pathlib.Path({str(self.root / 'started')!r}).touch(); time.sleep(10)"]}))
        proc = subprocess.Popen([sys.executable, str(RUNNER), 'run', '--spec', str(self.spec)], env=self.env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        import time, signal
        try:
            deadline = time.monotonic() + 3
            while not (self.root / 'started').exists() and proc.poll() is None and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue((self.root / 'started').exists())
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
        self.run_job(1, label='interrupted')
        self.assertEqual(len(self.events()), 1)
        self.run_job(1, label='interrupted')
        self.assertEqual(len(self.events()), 1)


if __name__ == '__main__':
    unittest.main()
