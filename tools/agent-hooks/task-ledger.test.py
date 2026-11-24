#!/usr/bin/env python3
"""Behavior tests for the private task ledger (stdlib only)."""
import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).with_name("task-ledger.py")


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        # Hooks consume session metadata from the environment. A developer's
        # parent job, role or model must never become part of these fixtures.
        # HOME/XDG also isolate the deferred-work observer called by the hook.
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("HERDR", "CLAUDE", "CODEX", "MUNICE", "GIT_", "AGENT_", "XDG_"))}
        self.env.update(HOME=str(self.root / "home"),
                        XDG_STATE_HOME=str(self.root / "xdg-state"),
                        XDG_CONFIG_HOME=str(self.root / "xdg-config"),
                        XDG_DATA_HOME=str(self.root / "xdg-data"),
                        XDG_CACHE_HOME=str(self.root / "xdg-cache"),
                        AGENT_TASK_LEDGER_DIR=str(self.root / "state"))
        self.codex = self.root / "codex"
        self.claude = self.root / "claude"
        self.codex.mkdir()
        self.claude.mkdir()

    def cli(self, *args, payload=None, env=None):
        result = subprocess.run([sys.executable, str(SCRIPT), *args],
                                input=json.dumps(payload) if payload is not None else "",
                                capture_output=True, text=True, env=env or self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout) if result.stdout.strip() else None

    def event(self, kind, job, **fields):
        args = ["record", kind, "--job-id", job]
        fields.setdefault("at", "2026-01-02T12:00:00Z")
        for key, value in fields.items():
            args += ["--" + key.replace("_", "-"), str(value)]
        return self.cli(*args)

    def report(self, *args):
        return self.cli("report", "--since", "2026-01-02", "--until", "2026-01-03",
                        "--codex-root", str(self.codex), "--claude-root", str(self.claude), *args)

    def jsonl(self, root, name, rows):
        (root / name).write_text("".join(json.dumps(row) + "\n" for row in rows))

    def test_concurrent_append_and_idempotency_preserve_every_event(self):
        # Losing an append or allowing the same id twice must fail this test.
        def append(i):
            return self.event("request", "job-" + str(i), event_id="event-" + str(i))
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(append, list(range(20)) * 2))
        data = self.report()
        self.assertEqual(data["jobs"], 20)
        self.assertEqual(data["events"], 20)
        self.assertEqual(data["tokens"]["missing_jobs"], 20)
        self.assertIsNone(data["tokens"]["inclusive_per_job"])

    def test_cumulative_codex_and_duplicate_claude_usage_include_children_once(self):
        self.event("request", "root")
        self.event("run", "root", session_id="cx", model="codex")
        self.event("run", "child", parent_job_id="root", session_id="cl", model="sonnet")
        self.event("check", "root", check_id="review", session_id="cl", verdict="fail")
        self.event("complete", "root", verdict="pass")
        usage1 = {"input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 20, "total_tokens": 120}
        usage2 = {"input_tokens": 250, "cached_input_tokens": 100, "output_tokens": 50, "total_tokens": 300}
        self.jsonl(self.codex, "run.jsonl", [
            {"type": "session_meta", "payload": {"id": "cx"}},
            *[{"type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": u}}}
              for u in [usage1, usage1, usage2, usage2]]])
        message = {"type": "assistant", "sessionId": "cl", "message": {"id": "msg1", "usage": {
            "input_tokens": 10, "cache_read_input_tokens": 30, "cache_creation_input_tokens": 20,
            "output_tokens": 5}}}
        self.jsonl(self.claude, "run.jsonl", [message, message])
        data = self.report()
        self.assertEqual(data["jobs"], 1)
        self.assertEqual(data["tokens"]["inclusive"], 365)
        self.assertEqual(data["tokens"]["exclusive"], 215)
        self.assertEqual(data["tokens"]["inclusive_per_job"], 365)
        self.assertEqual(data["check_failure"]["numerator"], 1)
        self.assertEqual(data["check_failure"]["denominator"], 1)
        self.assertEqual(data["check_failure"]["rate"], 1.0)

    def test_missing_records_stay_unknown_and_rates_expose_denominators(self):
        for job in ["a", "b", "c"]:
            self.event("request", job)
        self.event("run", "a", model="claude-sonnet", session_id="missing")
        self.event("reassign", "a", from_model="claude-sonnet", model="claude-opus", reason="check failed", attempt=2)
        self.event("complete", "a", verdict="fail")
        self.event("check", "a", check_id="c1", verdict="unknown")
        self.event("check", "b", check_id="c2", verdict="pass")
        self.event("complete", "b", verdict="unknown")
        data = self.report()
        self.assertEqual(data["no_result"], {"numerator": 2, "denominator": 3, "rate": 2 / 3})
        self.assertEqual(data["check_failure"]["numerator"], 0)
        self.assertEqual(data["check_failure"]["denominator"], 2)
        self.assertEqual(data["check_failure"]["unknown"], 1)
        self.assertIsNone(data["check_failure"]["rate"])
        self.assertEqual(data["check_failure"]["verified_only_rate"], 0.0)
        self.assertEqual(data["sonnet_to_opus"]["numerator"], 1)
        self.assertEqual(data["sonnet_to_opus"]["denominator"], 1)
        self.assertIsNone(data["sonnet_to_opus"]["rate"])
        self.assertEqual(data["sonnet_to_opus"]["model_unknown_jobs"], ["b", "c"])
        self.assertEqual(data["tokens"]["missing_jobs"], 3)
        self.assertEqual(data["missing"]["run_jobs"], ["b", "c"])
        self.assertEqual(data["missing"]["session_ids"], ["missing"])

    def test_hook_inherits_job_by_parent_session_and_never_infers_success(self):
        self.event("request", "job")
        self.event("run", "job", session_id="parent", model="sonnet")
        self.cli("hook", "SessionStart", payload={"session_id": "child", "parent_session_id": "parent"})
        self.cli("hook", "Stop", payload={"session_id": "child"})
        data = self.report()
        # Hook time is outside the cohort day, but linked sessions still count as missing.
        self.assertEqual(data["missing"]["session_ids"], ["child", "parent"])
        self.assertEqual(data["no_result"]["numerator"], 1)
        self.cli("hook", "Stop", payload={"session_id": "unmapped"})
        self.assertEqual(self.report()["jobs"], 1)
        bad = subprocess.run([sys.executable, str(SCRIPT), "hook", "Stop"], input="{broken",
                             env=self.env, text=True, capture_output=True)
        self.assertEqual((bad.returncode, bad.stdout), (0, ""))

    def test_compare_cohorts_and_corrupt_lines_are_visible(self):
        self.event("request", "before", at="2026-01-01T23:59:59Z")
        self.event("request", "after", at="2026-01-02T00:00:00Z")
        path = self.root / "state" / "events.jsonl"
        with path.open("a") as stream:
            stream.write("{broken}\n")
            stream.write(json.dumps({"kind": "run", "job_id": "incomplete", "at": "2026-01-02T12:00:00Z"}) + "\n")
        data = self.cli("compare", "--baseline", "2026-01-01", "--trial", "2026-01-02",
                        "--codex-root", str(self.codex), "--claude-root", str(self.claude), "--timezone", "UTC")
        self.assertEqual(data["baseline"]["job_ids"], ["before"])
        self.assertEqual(data["trial"]["job_ids"], ["after"])
        self.assertEqual(data["trial"]["missing"]["corrupt_event_lines"], 2)

    def test_stop_requires_explicit_marker_and_records_check_separately(self):
        self.event("request", "job")
        env = dict(self.env, HERDR_TASK_ID="job", HERDR_TASK_ROLE="check", HERDR_CHECK_ID="review-1")
        self.cli("hook", "Stop", payload={"session_id": "check", "last_assistant_message": "Looks good"}, env=env)
        self.assertEqual(self.report()["check_failure"]["unknown"], 1)
        self.cli("hook", "Stop", payload={"session_id": "check", "last_assistant_message": "TASK_CHECK: fail"}, env=env)
        self.assertEqual(self.report()["check_failure"]["numerator"], 1)
        self.assertEqual(self.report()["check_failure"]["unknown"], 0)
        env["HERDR_TASK_ROLE"] = "worker"
        self.cli("hook", "Stop", payload={"session_id": "worker", "last_assistant_message": "TASK_RESULT: pass"}, env=env)
        self.assertEqual(self.report()["no_result"]["numerator"], 0)

    def test_permission_block_is_unknown_and_preserved_in_check_event(self):
        self.event('request', 'job')
        env = dict(self.env, HERDR_TASK_ID='job', HERDR_TASK_ROLE='check', HERDR_CHECK_ID='blocked-check')
        text = ('TASK_CHECK: unknown\nblocked_reason: OTHER_DENIED\n'
                'blocked_command: crontab -l\nretry_target: claude')
        self.cli('hook', 'Stop', payload={'session_id': 'check', 'last_assistant_message': text}, env=env)
        rows = [json.loads(line) for line in (self.root / 'state/events.jsonl').read_text().splitlines()]
        result = next(row for row in rows if row['kind'] == 'check')
        self.assertEqual(result['verdict'], 'unknown')
        self.assertEqual(result['blocked_reason'], 'OTHER_DENIED')
        self.assertEqual(result['blocked_command'], 'crontab -l')
        self.assertEqual(result['retry_target'], 'claude')
        self.assertEqual(self.report()['check_failure']['numerator'], 0)
        self.assertEqual(self.report()['check_failure']['unknown'], 1)

    def test_permission_details_update_an_existing_unknown_outcome(self):
        self.event('request', 'job')
        env = dict(self.env, HERDR_TASK_ID='job', HERDR_TASK_ROLE='check')
        for details in ('', '\nblocked_reason: SIGNAL_DENIED\nblocked_command: ps -A\nretry_target: claude'):
            self.cli('hook', 'Stop', payload={'session_id': 'check', 'last_assistant_message': 'TASK_CHECK: unknown' + details}, env=env)
        rows = [json.loads(line) for line in (self.root / 'state/events.jsonl').read_text().splitlines()]
        self.assertEqual([row for row in rows if row['kind'] == 'check'][-1]['blocked_command'], 'ps -A')
        self.assertEqual(self.report()['check_failure']['unknown'], 1)

    def test_unknown_metadata_can_clear_without_duplicate_hook_results(self):
        self.event('request', 'job')
        env = dict(self.env, HERDR_TASK_ID='job', HERDR_TASK_ROLE='check')
        blocked = '\nblocked_reason: SIGNAL_DENIED\nblocked_command: ps -A\nretry_target: claude'
        for details in ('', blocked, '', ''):
            self.cli('hook', 'Stop', payload={'session_id': 'check', 'last_assistant_message': 'TASK_CHECK: unknown' + details}, env=env)
        rows = [json.loads(line) for line in (self.root / 'state/events.jsonl').read_text().splitlines()]
        checks = [row for row in rows if row['kind'] == 'check']
        self.assertEqual(len(checks), 3)
        self.assertNotIn('blocked_reason', checks[-1])

    def test_explicit_stop_replay_does_not_restore_an_old_block(self):
        self.event('request', 'job')
        env = dict(self.env, HERDR_TASK_ID='job', HERDR_TASK_ROLE='check')
        blocked = dict(session_id='check', hook_event_id='stop-A', last_assistant_message=(
            'TASK_CHECK: unknown\nblocked_reason: SIGNAL_DENIED\nblocked_command: ps -A\nretry_target: claude'))
        passed = dict(session_id='check', hook_event_id='stop-B', last_assistant_message='TASK_CHECK: pass')
        for payload in (blocked, passed, blocked):
            self.cli('hook', 'Stop', payload=payload, env=env)
        rows = [json.loads(line) for line in (self.root / 'state/events.jsonl').read_text().splitlines()]
        checks = [row for row in rows if row['kind'] == 'check']
        self.assertEqual(len(checks), 2)
        self.assertEqual(checks[-1]['verdict'], 'pass')

    def test_child_without_session_keeps_token_total_unknown(self):
        self.event("request", "root")
        self.event("run", "root", session_id="cx")
        self.event("run", "child", parent_job_id="root")
        self.jsonl(self.codex, "run.jsonl", [
            {"type": "session_meta", "payload": {"id": "cx"}},
            {"type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {
                "input_tokens": 10, "output_tokens": 3}}}}])
        data = self.report()
        self.assertIsNone(data["tokens"]["inclusive_per_job"])
        self.assertEqual(data["tokens"]["known_inclusive"], 13)
        self.assertEqual(data["missing"]["session_job_ids"], ["child"])

    def test_unassociated_transcripts_make_missing_baseline_records_visible(self):
        self.jsonl(self.codex, "unlinked.jsonl", [
            {"timestamp": "2026-01-02T12:00:00Z", "type": "session_meta", "payload": {"id": "unlinked"}}])
        data = self.report()
        self.assertEqual(data["jobs"], 0)
        self.assertEqual(data["missing"]["unassociated_session_ids"], ["unlinked"])
        self.assertIsNone(data["no_result"]["rate"])

    def test_job_for_session_returns_only_the_stable_id(self):
        self.event("run", "stable-job", session_id="session")
        found = subprocess.run([sys.executable, str(SCRIPT), "job-for-session", "session"],
                               env=self.env, capture_output=True, text=True)
        self.assertEqual((found.returncode, found.stdout), (0, "stable-job\n"))
        absent = subprocess.run([sys.executable, str(SCRIPT), "job-for-session", "absent"],
                                env=self.env, capture_output=True, text=True)
        self.assertEqual((absent.returncode, absent.stdout), (1, ""))

    def test_known_zero_and_missing_usage_are_different_and_private(self):
        self.event("request", "zero")
        self.event("run", "zero", session_id="zero")
        self.event("request", "empty")
        self.event("run", "empty", session_id="empty")
        self.jsonl(self.codex, "zero.jsonl", [
            {"type": "session_meta", "payload": {"id": "zero"}},
            {"type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {
                "input_tokens": 0, "output_tokens": 0}}}}])
        self.jsonl(self.codex, "empty.jsonl", [{"type": "session_meta", "payload": {"id": "empty"}}])
        data = self.report()
        self.assertEqual({r["job_id"]: r["inclusive_tokens"] for r in data["job_details"]}, {"zero": 0, "empty": None})
        self.assertEqual(data["missing"]["check_jobs"], ["empty", "zero"])
        self.assertEqual((self.root / "state").stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.root / "state" / "events.jsonl").stat().st_mode & 0o777, 0o600)

    def test_boundary_censoring_does_not_hide_later_result_or_pending_work(self):
        self.event("request", "later")
        self.event("complete", "later", verdict="pass", at="2026-01-04T12:00:00Z")
        self.event("request", "pending")
        data = self.report()
        self.assertEqual(data["no_result"]["numerator"], 1)
        self.assertEqual(data["missing"]["pending_result_jobs"], ["pending"])
        self.assertEqual(data["censored_at_until_job_ids"], ["later", "pending"])

    def test_claude_stop_reads_final_text_and_model_from_transcript(self):
        self.event("request", "job")
        transcript = self.claude / "session.jsonl"
        self.jsonl(self.claude, "session.jsonl", [
            {"type": "user", "sessionId": "session", "message": {"content": "private request"}},
            {"type": "assistant", "sessionId": "session", "message": {
                "id": "message", "model": "claude-sonnet-4-6", "content": [
                    {"type": "thinking", "thinking": "private reasoning"},
                    {"type": "text", "text": "Done.\nTASK_RESULT: pass"}],
                "usage": {"input_tokens": 10, "output_tokens": 5}}}])
        env = dict(self.env, HERDR_TASK_ID="job")
        self.cli("hook", "Stop", payload={"session_id": "session", "transcript_path": str(transcript)}, env=env)
        data = self.report()
        self.assertEqual(data["no_result"]["numerator"], 0)
        self.assertEqual(data["sonnet_to_opus"]["denominator"], 1)
        stored = (self.root / "state" / "events.jsonl").read_text()
        self.assertNotIn("private request", stored)
        self.assertNotIn("private reasoning", stored)

    def test_report_uses_transcript_model_when_run_model_is_missing(self):
        self.event("request", "job")
        self.event("run", "job", session_id="session")
        self.jsonl(self.claude, "session.jsonl", [{"type": "assistant", "sessionId": "session", "message": {
            "id": "msg", "model": "claude-sonnet-4-6", "usage": {"input_tokens": 1, "output_tokens": 1}}}])
        self.assertEqual(self.report()["sonnet_to_opus"]["denominator"], 1)

    def test_malformed_session_record_does_not_turn_unknown_into_zero(self):
        self.event("request", "job")
        self.event("run", "job", session_id="session")
        self.jsonl(self.codex, "session.jsonl", [
            {"type": "session_meta", "payload": {"id": "session"}},
            {"type": "event_msg", "payload": ["unexpected-schema"]}])
        data = self.report()
        self.assertIsNone(data["tokens"]["inclusive"])
        self.assertEqual(data["missing"]["corrupt_session_lines"], 1)

    def test_null_token_counter_is_unknown_instead_of_zero(self):
        self.event("request", "job")
        self.event("run", "job", session_id="session")
        self.jsonl(self.codex, "session.jsonl", [
            {"type": "session_meta", "payload": {"id": "session"}},
            {"type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {
                "input_tokens": None, "output_tokens": 2}}}}])
        self.assertIsNone(self.report()["tokens"]["inclusive"])

    def test_unrecorded_model_transition_is_unknown_instead_of_no_reassignment(self):
        self.event("request", "job")
        self.event("run", "job", session_id="s1", model="claude-sonnet")
        self.event("run", "job", session_id="s2", model="claude-opus", attempt=2)
        data = self.report()
        self.assertIsNone(data["sonnet_to_opus"]["rate"])
        self.assertEqual(data["sonnet_to_opus"]["unrecorded_transition_job_ids"], ["job"])
        self.assertEqual(data["sonnet_to_opus"]["unknown_reason_jobs"], 1)
        self.assertIsNone(data["check_failure"]["rate"])
        self.assertEqual(data["check_failure"]["missing_job_count"], 1)

    def test_native_child_success_cannot_complete_its_parent_request(self):
        self.event("request", "root")
        self.event("run", "root", session_id="parent", model="codex")
        env = dict(self.env, HERDR_TASK_ID="root")
        payload = {"session_id": "native-child", "parent_session_id": "parent"}
        self.cli("hook", "SessionStart", payload=payload, env=env)
        self.cli("hook", "Stop", payload={"session_id": "native-child", "last_assistant_message": "TASK_RESULT: pass"}, env=env)
        data = self.report()
        self.assertEqual(data["jobs"], 1)
        self.assertEqual(data["no_result"]["numerator"], 1)
        self.assertEqual(data["job_details"][0]["verdict"], "unknown")
        rows = [json.loads(line) for line in (self.root / "state" / "events.jsonl").read_text().splitlines()]
        child_rows = [row for row in rows if row.get("session_id") == "native-child"]
        self.assertTrue(child_rows)
        self.assertTrue(all(row["job_id"] != "root" and row["parent_job_id"] == "root" for row in child_rows))
        self.assertEqual(len({row["job_id"] for row in child_rows}), 1)
        self.assertEqual([row["verdict"] for row in child_rows if row["kind"] == "complete"], ["pass"])

    def test_dedicated_child_job_is_preserved_when_parent_session_is_supplied(self):
        self.event("request", "root")
        self.event("run", "root", session_id="parent")
        self.event("request", "dedicated", parent_job_id="root")
        env = dict(self.env, HERDR_TASK_ID="dedicated", HERDR_PARENT_TASK_ID="root")
        payload = {"session_id": "dedicated-session", "parent_session_id": "parent"}
        self.cli("hook", "SessionStart", payload=payload, env=env)
        self.cli("hook", "Stop", payload=dict(payload, last_assistant_message="TASK_RESULT: pass"), env=env)
        rows = [json.loads(line) for line in (self.root / "state" / "events.jsonl").read_text().splitlines()]
        self.assertEqual({row["job_id"] for row in rows if row.get("session_id") == "dedicated-session"}, {"dedicated"})
        self.assertEqual(self.report()["no_result"]["numerator"], 1)

    def test_native_grandchild_inheriting_root_environment_keeps_parent_chain(self):
        self.event("request", "root")
        self.event("run", "root", session_id="parent")
        env = dict(self.env, HERDR_TASK_ID="root")
        self.cli("hook", "SessionStart", payload={"session_id": "child", "parent_session_id": "parent"}, env=env)
        self.cli("hook", "Stop", payload={"session_id": "grandchild", "parent_session_id": "child",
                                            "last_assistant_message": "TASK_RESULT: pass"}, env=env)
        rows = [json.loads(line) for line in (self.root / "state" / "events.jsonl").read_text().splitlines()]
        child_job = next(row["job_id"] for row in rows if row.get("session_id") == "child")
        grandchild = next(row for row in rows if row.get("session_id") == "grandchild" and row["kind"] == "complete")
        self.assertNotEqual(grandchild["job_id"], "root")
        self.assertNotEqual(grandchild["job_id"], child_job)
        self.assertEqual(grandchild["parent_job_id"], child_job)
        self.assertEqual(self.report()["no_result"]["numerator"], 1)

    def test_codex_native_stop_restores_parent_from_session_meta(self):
        self.event("request", "root")
        self.event("run", "root", session_id="parent")
        self.jsonl(self.codex, "child.jsonl", [{"type": "session_meta", "payload": {
            "id": "child", "parent_thread_id": "parent", "source": {
                "subagent": {"thread_spawn": {"parent_thread_id": "parent", "depth": 1}}}}}])
        self.cli("hook", "Stop", payload={"session_id": "child", "transcript_path": str(self.codex / "child.jsonl"),
                                            "last_assistant_message": "TASK_RESULT: pass"},
                 env=dict(self.env, HERDR_TASK_ID="root"))
        self.assertEqual(self.report()["no_result"]["numerator"], 1)
        rows = [json.loads(line) for line in (self.root / "state" / "events.jsonl").read_text().splitlines()]
        complete = next(row for row in rows if row["kind"] == "complete")
        self.assertNotEqual(complete["job_id"], "root")
        self.assertEqual(complete["parent_job_id"], "root")

    def test_report_discovers_unhooked_native_child_and_grandchild_usage(self):
        self.event("request", "root")
        self.event("run", "root", session_id="parent", model="codex")
        for sid, parent, input_tokens, output_tokens in [("parent", None, 10, 2), ("child", "parent", 20, 3), ("grandchild", "child", 30, 4)]:
            meta = {"id": sid}
            if parent:
                meta["source"] = {"subagent": {"thread_spawn": {"parent_thread_id": parent}}}
            self.jsonl(self.codex, sid + ".jsonl", [
                {"timestamp": "2026-01-02T12:00:00Z", "type": "session_meta", "payload": meta},
                {"type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {
                    "input_tokens": input_tokens, "output_tokens": output_tokens}}}}])
        data = self.report()
        self.assertEqual(data["jobs"], 1)
        self.assertEqual(data["tokens"]["inclusive"], 69)
        self.assertEqual(data["job_details"][0]["session_ids"], ["child", "grandchild", "parent"])
        self.assertEqual(data["discovered_session_ids"], ["child", "grandchild"])
        self.assertEqual(data["missing"]["unassociated_session_ids"], [])

    def test_corrupt_transcript_keeps_known_subtotal_but_not_complete_total(self):
        self.event("request", "root")
        self.event("run", "root", session_id="clean", model="codex")
        for sid, input_tokens in [("clean", 10), ("damaged", 100)]:
            self.jsonl(self.codex, sid + ".jsonl", [
                {"type": "session_meta", "payload": {"id": sid}},
                {"type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {
                    "input_tokens": input_tokens, "output_tokens": 10}}}}])
        with (self.codex / "damaged.jsonl").open("a") as stream:
            stream.write('{"type":"event_msg", "payload":')
        # An unrelated damaged transcript must not invalidate this cohort.
        self.assertEqual(self.report()["tokens"]["inclusive"], 20)
        self.event("run", "root", session_id="damaged", attempt=2, model="codex")
        data = self.report()
        self.assertIsNone(data["tokens"]["inclusive"])
        self.assertEqual(data["tokens"]["known_inclusive"], 130)
        self.assertEqual(data["tokens"]["covered_jobs"], 0)
        self.assertEqual(data["missing"]["incomplete_session_ids"], ["damaged"])

    def test_claude_usage_without_message_id_keeps_total_unknown(self):
        self.event("request", "root")
        self.event("run", "root", session_id="session")
        self.jsonl(self.claude, "session.jsonl", [
            {"type": "assistant", "sessionId": "session", "message": {
                "id": "valid", "usage": {"input_tokens": 10, "output_tokens": 2}}},
            {"type": "assistant", "sessionId": "session", "message": {
                "usage": {"input_tokens": 50, "output_tokens": 5}}}])
        data = self.report()
        self.assertIsNone(data["tokens"]["inclusive"])
        self.assertEqual(data["tokens"]["known_inclusive"], 12)
        self.assertEqual(data["missing"]["incomplete_session_ids"], ["session"])

    def test_truncated_utf8_session_keeps_known_usage_and_report_available(self):
        self.event("request", "root")
        self.event("run", "root", session_id="session")
        self.jsonl(self.codex, "session.jsonl", [
            {"type": "session_meta", "payload": {"id": "session"}},
            {"type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {
                "input_tokens": 10, "output_tokens": 2}}}}])
        with (self.codex / "session.jsonl").open("ab") as stream:
            stream.write(b'{"text":"\xf0\x9f')
        data = self.report()
        self.assertIsNone(data["tokens"]["inclusive"])
        self.assertEqual(data["tokens"]["known_inclusive"], 12)
        self.assertEqual(data["missing"]["incomplete_session_ids"], ["session"])

    # api#1508: a multi-step job names, per phase, the Macs allowed to work on it and who answers for it.
    def host_check(self, *args, role=None, **env):
        home = self.root / "home"
        marker = home / ".config" / "munice" / "host-role"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.unlink(missing_ok=True)
        if role is not None:
            marker.write_text(role + "\n")
        result = subprocess.run([sys.executable, str(SCRIPT), "host-check", *args], capture_output=True, text=True,
                                env={**self.env, **env})
        return result.returncode, (json.loads(result.stdout) if result.stdout.strip() else None), result.stderr

    def phase(self, job, phase, hosts, **fields):
        return self.event("phase", job, phase=phase, allowed_hosts=hosts, **fields)

    def refuses(self, *args):
        result = subprocess.run([sys.executable, str(SCRIPT), "record", *args], capture_output=True, text=True, env=self.env)
        self.assertEqual(result.returncode, 2, result.stdout)
        return result.stderr

    def test_phase_allows_listed_hosts_and_refuses_the_rest(self):
        self.phase("job", "분석", "세션맥,휴대맥", owner="lead")
        self.phase("job", "상주 잡", "세션맥", owner="lead")
        rc, data, _ = self.host_check("--job-id", "job", "--phase", "분석")
        self.assertEqual((rc, data["status"], data["host"]), (0, "allowed", "세션맥"), "no host-role file is 세션맥")
        rc, data, _ = self.host_check("--job-id", "job", "--phase", "분석", role="portable")
        self.assertEqual((rc, data["status"], data["host"]), (0, "allowed", "휴대맥"), "the carried Mac may share work, not just 세션맥")
        rc, data, message = self.host_check("--job-id", "job", "--phase", "상주 잡", role="portable")
        self.assertEqual((rc, data["status"]), (3, "refused"))
        for word in ("상주 잡", "세션맥", "휴대맥", "lead", "record handoff", "--artifacts", "--done-criteria"):
            self.assertIn(word, message)
        rc, data, _ = self.host_check("--job-id", "job", "--phase", "상주 잡", role="ambient-runner")
        self.assertEqual((rc, data["host"]), (3, "집맥"))

    def test_current_phase_is_the_latest_one_recorded(self):
        self.phase("job", "분석", "세션맥,휴대맥")
        self.phase("job", "production", "세션맥", at="2026-01-02T13:00:00Z")
        rc, data, _ = self.host_check("--job-id", "job", role="portable")
        self.assertEqual((rc, data["phase"]), (3, "production"))
        self.phase("job", "production", "세션맥,휴대맥", at="2026-01-02T14:00:00Z")
        rc, data, _ = self.host_check("--job-id", "job", role="portable")
        self.assertEqual((rc, data["status"]), (0, "allowed"), "a phase written again replaces its hosts")

    def test_nothing_recorded_means_no_restriction(self):
        for args in (["--job-id", "unknown"], ["--job-id", "job", "--phase", "never-written"]):
            self.event("request", "job")
            rc, data, _ = self.host_check(*args, role="portable")
            self.assertEqual((rc, data["status"]), (0, "unrecorded"), args)
        # No ledger file at all, and a damaged line next to a good phase.
        self.assertEqual(self.host_check("--job-id", "job")[0], 0)
        self.phase("job", "분석", "세션맥")
        with (self.root / "state" / "events.jsonl").open("a") as stream:
            stream.write("{not json\n")
        self.assertEqual(self.host_check("--job-id", "job", role="portable")[0], 3, "the good line still counts")

    def test_child_job_follows_the_nearest_phase_up_its_parents(self):
        self.event("request", "root")
        self.phase("root", "분석", "세션맥")
        self.event("request", "child", parent_job_id="root")
        self.event("request", "grandchild", parent_job_id="child")
        self.assertEqual(self.host_check("--job-id", "grandchild", role="portable")[0], 3)
        self.phase("child", "분석", "세션맥,휴대맥")
        self.assertEqual(self.host_check("--job-id", "grandchild", role="portable")[0], 0, "the nearest phase wins")

    def test_handoff_needs_artifacts_and_done_criteria_and_opens_the_host(self):
        self.phase("job", "상주 잡", "세션맥", owner="lead")
        self.assertIn("--artifacts", self.refuses("handoff", "--job-id", "job", "--phase", "상주 잡", "--to-host", "휴대맥",
                                                  "--done-criteria", "시험 통과"))
        self.assertIn("--done-criteria", self.refuses("handoff", "--job-id", "job", "--phase", "상주 잡", "--to-host", "휴대맥",
                                                      "--artifacts", "PR 주소"))
        self.assertIn("--to-host", self.refuses("handoff", "--job-id", "job", "--phase", "상주 잡",
                                                "--artifacts", "PR 주소", "--done-criteria", "시험 통과"))
        self.assertIn("--allowed-hosts", self.refuses("phase", "--job-id", "job", "--phase", "x"))
        self.assertIn("--phase", self.refuses("phase", "--job-id", "job", "--allowed-hosts", "세션맥"))
        self.assertEqual(self.host_check("--job-id", "job", role="portable")[0], 3, "refused handoffs changed nothing")
        self.event("handoff", "job", phase="상주 잡", from_host="세션맥", to_host="휴대맥", artifacts="PR 주소",
                   done_criteria="시험 통과", at="2026-01-02T13:00:00Z")
        rc, data, _ = self.host_check("--job-id", "job", role="portable")
        self.assertEqual((rc, data["status"]), (0, "allowed"))
        self.assertEqual(data["allowed"], ["세션맥", "휴대맥"])
        self.assertEqual(data["handoff"], {"artifacts": "PR 주소", "done_criteria": "시험 통과"})
        self.assertEqual(self.host_check("--job-id", "job", role="ambient-runner")[0], 3, "only the named Mac opened")

    def test_phase_events_never_break_the_usage_report(self):
        self.event("request", "root")
        self.phase("root", "분석", "세션맥")
        self.assertEqual(self.report()["jobs"], 1)


if __name__ == "__main__":
    unittest.main()
