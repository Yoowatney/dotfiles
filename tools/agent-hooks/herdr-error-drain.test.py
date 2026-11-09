#!/usr/bin/env python3
"""Transfer ordering and failure behavior of the terminal error relay."""
import importlib.util
from pathlib import Path
import subprocess
import unittest
import json
import uuid

PATH = Path(__file__).with_name("herdr-error-drain.py")
spec = importlib.util.spec_from_file_location("error_drain", PATH)
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)


class RelayTests(unittest.TestCase):
    def run_flow(self, failures=()):
        calls = []
        def run(cmd, **kwargs):
            op = next(x for x in cmd if x.startswith("--")) if cmd[0] != "ssh" else cmd[-1].split()[-1]
            calls.append((cmd[0], op, kwargs.get("input")))
            if op in failures:
                raise subprocess.TimeoutExpired(cmd, 1)
            output = '[{"id":"00000000-0000-4000-8000-000000000001","title":"error","body":"delivery failed","created_at":"2026-11-09T12:00:00+00:00"}]' if op == "--export" else "{}"
            return subprocess.CompletedProcess(cmd, 0, output, "")
        relay.drain("test-host", run=run)
        return calls

    def test_durable_import_precedes_ack(self):
        calls = self.run_flow()
        self.assertEqual([x[1] for x in calls], ["--export", "--import", "00000000-0000-4000-8000-000000000001", "--drain"])
        self.assertIn('"id"', calls[1][2])

    def test_export_timeout_still_drains_local(self):
        self.assertEqual([x[1] for x in self.run_flow(("--export",))], ["--export", "--drain"])

    def test_import_failure_never_acknowledges(self):
        self.assertEqual([x[1] for x in self.run_flow(("--import",))], ["--export", "--import", "--drain"])

    def test_ack_timeout_still_drains_local(self):
        self.assertEqual(self.run_flow(("00000000-0000-4000-8000-000000000001",))[-1][1], "--drain")

    def test_no_remote_only_local(self):
        calls = []
        def run(cmd, **kw):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "{}", "")
        relay.drain("", run=run)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][-1], "--drain")

    def test_invalid_remote_data_never_imports_or_acks(self):
        calls = []
        def run(cmd, **kw):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, '{"wrong":true}', "")
        relay.drain("test-host", run=run)
        self.assertEqual(len(calls), 2)

    def test_backlog_larger_than_import_limit_is_batched(self):
        rows = [dict(id=str(uuid.uuid4()), title="error", body="failed", created_at="2026-11-09T12:00:00+00:00") for _ in range(1001)]
        imported, acknowledged = [], []
        def run(cmd, **kw):
            if cmd[0] == "ssh" and cmd[-1].endswith("--export"):
                return subprocess.CompletedProcess(cmd, 0, json.dumps(rows), "")
            if "--import" in cmd:
                imported.extend(json.loads(kw["input"]))
            if cmd[0] == "ssh" and "--ack " in cmd[-1]:
                acknowledged.extend(cmd[-1].split("--ack ", 1)[1].split())
            return subprocess.CompletedProcess(cmd, 0, "{}", "")
        relay.drain("test-host", run=run)
        self.assertEqual(len(imported), 1000)
        self.assertEqual(acknowledged, [r["id"] for r in rows[:1000]])


if __name__ == "__main__":
    unittest.main()
