#!/usr/bin/env python3
"""Offline boundary test: real refresh + relay queues; only herdr/agent are fixtures."""
import importlib.util
import fcntl
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent


def until(predicate, timeout=15):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(.03)
    raise AssertionError("boundary did not arrive")


class RefreshRace(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state = self.root / "state"
        for name in ("orchestrator", "agent-handoff"):
            (self.state / name).mkdir(parents=True)
        (self.state / "orchestrator/main").write_text("main-terminal")
        self.transcript = self.root / "old-session.jsonl"
        self.transcript.touch()
        (self.state / "agent-handoff/w1:p1.md.transcript").write_text(str(self.transcript))
        (self.root / "session").write_text("old-session")
        (self.root / "status").write_text("working")
        fake = self.root / "herdr"
        fake.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys, time
r = pathlib.Path(os.environ["FIXTURE"])
a = sys.argv[1:]
sid = (r / "session").read_text()
main = dict(pane_id="w1:p1", terminal_id="main-terminal", agent="claude", agent_session=dict(value=sid), agent_status=(r / "status").read_text(), label="main", scroll=dict(offset_from_bottom=0))
sender = dict(pane_id="w1:p9", terminal_id="sender-terminal", agent="codex", agent_session=dict(value="sender"), label="worker")
if a[:2] == ["pane", "list"]: print(json.dumps(dict(result=dict(panes=[main, sender]))))
elif a[:2] == ["pane", "get"]:
    if a[2] == "w1:p1" and (r / "get-fails").exists():
        (r / "get-failed").touch()
        raise SystemExit(1)
    print(json.dumps(dict(result=dict(pane=main if a[2] == "w1:p1" else sender))))
elif a[:2] == ["pane", "read"]:
    print("─" * 40 + "\\n❯ " + ("typed draft" if (r / "full").exists() else "") + "\\n" + "─" * 40 + "\\n  footer")
elif a[:2] == ["tab", "list"]: print('{"result":{"tabs":[]}}')
elif a[:2] == ["session", "list"]: print('{"sessions":[{"name":"default","running":true,"socket_path":"/default/herdr.sock"}]}')
elif a[:2] == ["agent", "prompt"]:
    if a[2] != "w1:p1": raise SystemExit(0)
    text = a[3]
    with (r / "prompts").open("a") as f: f.write(json.dumps([sid, text]) + "\\n")
    if text == "/clear":
        (r / "clearing").touch()
        if (r / "return-before-clear").exists(): raise SystemExit(0)
        while not (r / "clear-continue").exists(): time.sleep(.02)
        (r / "session").write_text("new-session")
        (r / "status").write_text("idle")
    elif "[main-refresh]" in text:
        with (r / "old-session.jsonl").open("a") as f: f.write(json.dumps(dict(type="user", message=dict(content=text))) + "\\n")
        (r / "requested").touch()
        if not (r / "no-handoff").exists():
            time.sleep(.05)
            (r / "state/agent-handoff/w1:p1.md").write_text(text.split()[-1] + "\\nhandoff")
            if (r / "snapshot-fails").exists():
                (r / "state/agent-handoff/refresh-recovery").write_text("not a directory")
elif a[:2] == ["agent", "send-keys"]: raise SystemExit("must preserve typed draft")
else: raise SystemExit(2)
''')
        fake.chmod(0o755)
        self.env = {**os.environ, "FIXTURE": str(self.root), "HOME": str(self.root),
                    "XDG_STATE_HOME": str(self.state), "HERDR_BIN_PATH": str(fake),
                    "HERDR_PANE_ID": "w1:p9", "PANE_TELL_BATCH": "0",
                    "PANE_TELL_SETTLE": "0", "PANE_TELL_POLL": ".03", "PANE_TELL_WAIT": "30",
                    "HERDR_ERROR_NOTIFY": "/usr/bin/true", "MAIN_REFRESH_POLL": ".03",
                    "MAIN_REFRESH_DEADLINE": "30", "MAIN_REFRESH_HANDOFF_WAIT": "5"}
        for name in ("HERDR_SOCKET_PATH", "HERDR_SESSION", "MAIN_REFRESH_QUIET", "MAIN_REFRESH_PANE_TELL"):
            self.env.pop(name, None)
        self.proc = None

    def tearDown(self):
        (self.root / "clear-continue").touch()
        (self.root / "full").unlink(missing_ok=True)
        (self.root / "status").write_text("idle")
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        (self.state / "orchestrator/refresh-delivery.json").unlink(missing_ok=True)
        # Drain only this fixture's detached receivers before deleting its files.
        until(lambda: not list((self.state / "pane-tell/waiting").glob("*")), timeout=10)
        # Removing a payload precedes the supervisor's final metadata/log writes.
        # Wait for these fixture-owned processes, not just their payloads.
        def receivers_stopped():
            rows = subprocess.check_output(["/bin/ps", "-axo", "args="], text=True)
            return not any(str(self.state / "pane-tell") in row for row in rows.splitlines())
        until(receivers_stopped, timeout=10)
        self.tmp.cleanup()

    def tell(self, text, raw=False, cross=False):
        args = [str(HERE / "pane-tell"), *(["--raw"] if raw else []), "default/w1:p1" if cross else "w1:p1", text]
        env = {**self.env, "HERDR_SOCKET_PATH": "/sessions/workers/herdr.sock"} if cross else self.env
        return subprocess.check_output(args, env=env, text=True, timeout=10)

    def start(self):
        self.proc = subprocess.Popen(["/bin/bash", str(HERE / "main-refresh"), "--now"], env=self.env)

    def prompts(self):
        path = self.root / "prompts"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_busy_handoff_and_clear_preserve_all_messages(self):
        # Queue before refresh against the OLD session; this must also survive.
        (self.root / "full").touch()
        self.assertIn("queued", self.tell("before-refresh"))
        self.start()
        until(lambda: (self.state / "orchestrator/refresh-delivery.json").exists())
        self.tell("during-turn", raw=True)
        self.assertFalse((self.root / "requested").exists())
        (self.root / "full").unlink()
        # Fresh mtime continually resets the old quiet window.
        self.transcript.touch()
        # Force the handoff request through the durable queue as well: it must
        # bypass the parked backlog even if another sender holds the send lock.
        with (self.state / "pane-tell/sending/w1:p1.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            (self.root / "status").write_text("idle")
            until(lambda: any("[main-refresh]" in path.read_text()
                              for path in (self.state / "pane-tell/waiting").glob("*")))
        until(lambda: (self.root / "clearing").exists())
        self.assertIn("queued", self.tell("during-clear-report", cross=True))
        self.tell("during-clear-automatic")
        self.assertFalse(any("during-" in text for _, text in self.prompts()))
        (self.root / "clear-continue").touch()
        self.assertEqual(self.proc.wait(timeout=10), 0)
        until(lambda: len(self.prompts()) == 6)
        received = [(sid, text) for sid, text in self.prompts() if text != "/clear" and "[main-refresh]" not in text]
        self.assertEqual([sid for sid, _ in received], ["new-session"] * 4)
        for (_, text), expected in zip(received, ["before-refresh", "during-turn", "during-clear-report", "during-clear-automatic"]):
            self.assertTrue(text.endswith(expected), (text, expected))
        self.assertFalse(list((self.state / "pane-tell/undelivered").glob("*")))
        self.assertFalse(list((self.state / "agent-handoff/refresh-recovery").glob("*/active")))

    def test_workers_server_message_waits_for_new_main_session(self):
        # No older queue may mask a cross-server sender reading the wrong gate.
        (self.root / "status").write_text("idle")
        self.start()
        until(lambda: (self.root / "clearing").exists())
        self.assertIn("queued", self.tell("cross-server-report", cross=True))
        self.assertFalse(any(text.endswith("cross-server-report") for _, text in self.prompts()))
        (self.root / "clear-continue").touch()
        self.assertEqual(self.proc.wait(timeout=10), 0)
        until(lambda: any(text.endswith("cross-server-report") for _, text in self.prompts()))
        received = [(sid, text) for sid, text in self.prompts() if text.endswith("cross-server-report")]
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0][0], "new-session")

    def test_failed_handoff_releases_durable_messages_without_clear(self):
        (self.root / "no-handoff").touch()
        self.start()
        until(lambda: (self.state / "orchestrator/refresh-delivery.json").exists())
        self.tell("after-failed-handoff", raw=True)
        (self.root / "status").write_text("idle")
        until(lambda: (self.root / "requested").exists())
        self.assertEqual(self.proc.wait(timeout=10), 0)
        until(lambda: any(text == "after-failed-handoff" for _, text in self.prompts()))
        self.assertNotIn("/clear", [text for _, text in self.prompts()])
        self.assertEqual(self.prompts()[-1], ["old-session", "after-failed-handoff"])

    def test_transient_pane_lookup_does_not_cancel_active_handoff(self):
        self.start()
        until(lambda: (self.state / "orchestrator/refresh-delivery.json").exists())
        self.tell("ordinary-after-recovery", raw=True)
        with (self.state / "pane-tell/sending/w1:p1.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            (self.root / "status").write_text("idle")
            until(lambda: any("[main-refresh]" in path.read_text()
                              for path in (self.state / "pane-tell/waiting").glob("*")))
            (self.root / "get-fails").touch()
        until(lambda: (self.root / "get-failed").exists())
        self.assertFalse((self.root / "requested").exists())
        self.assertTrue(any("[main-refresh]" in path.read_text()
                            for path in (self.state / "pane-tell/waiting").glob("*")))
        (self.root / "get-fails").unlink()
        until(lambda: (self.root / "clearing").exists())
        (self.root / "clear-continue").touch()
        self.assertEqual(self.proc.wait(timeout=10), 0)
        until(lambda: any(text == "ordinary-after-recovery" for _, text in self.prompts()))
        self.assertEqual(sum("[main-refresh]" in text for _, text in self.prompts()), 1)
        self.assertEqual(self.prompts()[-1], ["new-session", "ordinary-after-recovery"])

    def test_skipped_queued_handoff_is_cancelled_after_manual_session_change(self):
        self.check_skipped_queued_handoff("manually-new-session")

    def test_skipped_queued_handoff_is_cancelled_in_original_session(self):
        self.check_skipped_queued_handoff("old-session")

    def test_old_queue_cannot_join_resumed_refresh_attempt(self):
        self.check_skipped_queued_handoff("old-session", resumed=True)

    def check_skipped_queued_handoff(self, session, resumed=False):
        self.env["MAIN_REFRESH_HANDOFF_WAIT"] = "1"
        self.start()
        until(lambda: (self.state / "orchestrator/refresh-delivery.json").exists())
        self.tell("ordinary-report", raw=True)
        with (self.state / "pane-tell/sending/w1:p1.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            (self.root / "status").write_text("idle")
            until(lambda: any("[main-refresh]" in path.read_text()
                              for path in (self.state / "pane-tell/waiting").glob("*")))
            self.tell("report-behind-request", raw=True)
            self.assertEqual(self.proc.wait(timeout=8), 0)
            # A manual switch after refresh skipped must not inherit its request.
            (self.root / "session").write_text(session)
            if resumed:
                # The durable token is reused by a later refresh watcher, but
                # its new gate must not revive the old attempt's queued copy.
                (self.state / "orchestrator/refresh-delivery.json").write_text(
                    json.dumps(dict(pid=os.getpid(), terminal="main-terminal", session=session)))
        if resumed:
            until(lambda: any("cancelled refresh request" in line for line in
                  (self.state / "pane-tell/log").read_text().splitlines()))
            (self.state / "orchestrator/refresh-delivery.json").unlink()
        until(lambda: not list((self.state / "pane-tell/waiting").glob("*")))
        self.assertEqual(self.prompts(), [[session, "ordinary-report"], [session, "report-behind-request"]])
        self.assertFalse(list((self.state / "pane-tell/undelivered").glob("*")))

    def test_clear_confirmation_timeout_recovers_handoff_and_relays(self):
        (self.root / "return-before-clear").touch()
        (self.root / "status").write_text("idle")
        self.env["MAIN_REFRESH_DEADLINE"] = "8"
        self.start()
        until(lambda: (self.root / "clearing").exists())
        self.tell("after-accepted-clear", raw=True)
        self.assertEqual(self.proc.wait(timeout=12), 0)
        self.assertTrue((self.state / "agent-handoff/w1:p1.md").exists())
        until(lambda: any(text == "after-accepted-clear" for _, text in self.prompts()), timeout=3)
        self.assertEqual(self.prompts()[-1], ["old-session", "after-accepted-clear"])
        self.tell("second-recovered-relay", raw=True)
        records = list((self.state / "agent-handoff/refresh-recovery").glob("*/handoff.md"))
        self.assertEqual(len(records), 1)
        original = records[0].read_text()
        self.assertIn("handoff", original)
        journal = [json.loads(line) for line in records[0].with_name("relays.jsonl").read_text().splitlines()]
        self.assertEqual([item["message"] for item in journal], ["after-accepted-clear", "second-recovered-relay"])
        self.assertEqual([item["session"] for item in journal], ["old-session"] * 2)
        (self.root / "session").write_text("new-session")
        # Discovery survives a consumed/expired ordinary handoff.
        (self.state / "agent-handoff/w1:p1.md").unlink()
        result = subprocess.check_output(["python3", "-I", str(HERE / "handoff-state.py"), "load"],
                                        env={**self.env, "HERDR_PANE_ID": "w1:p1"}, text=True)
        self.assertIn(str(records[0]), result)
        self.assertIn("relays.jsonl", result)
        self.assertEqual(records[0].read_text(), original)

    def test_dead_clear_owner_expires_and_record_failure_retains_queue(self):
        (self.root / "return-before-clear").touch()
        (self.root / "status").write_text("idle")
        self.env["MAIN_REFRESH_DEADLINE"] = "5"
        self.start()
        until(lambda: (self.root / "clearing").exists())
        gate = json.loads((self.state / "orchestrator/refresh-delivery.json").read_text())
        self.proc.kill()
        self.proc.wait(timeout=5)
        self.tell("owner-died", raw=True)
        until(lambda: any(text == "owner-died" for _, text in self.prompts()), timeout=7)
        folder = Path(gate["recovery"])
        journal = folder / "relays.jsonl"
        self.assertTrue((folder / "active").exists())
        first = journal.read_text()
        self.assertIn("owner-died", first)
        # A directory simulates an unwritable journal even when running as root.
        journal.unlink()
        journal.mkdir()
        self.tell("must-retain-on-record-failure", raw=True)
        time.sleep(.3)
        self.assertFalse(any(text == "must-retain-on-record-failure" for _, text in self.prompts()))
        self.assertTrue(list((self.state / "pane-tell/waiting").glob("*")))
        journal.rmdir()
        journal.write_text(first)
        until(lambda: any(text == "must-retain-on-record-failure" for _, text in self.prompts()))
        self.assertIn("must-retain-on-record-failure", journal.read_text())
        self.assertEqual(self.prompts()[-1][0], "old-session")

    def test_snapshot_failure_prevents_clear_and_preserves_handoff(self):
        (self.root / "snapshot-fails").touch()
        (self.root / "status").write_text("idle")
        self.start()
        self.assertEqual(self.proc.wait(timeout=10), 0)
        self.assertNotIn("/clear", [text for _, text in self.prompts()])
        self.assertTrue((self.state / "agent-handoff/w1:p1.md.skipped").exists())
        self.assertFalse((self.state / "orchestrator/refresh-delivery.json").exists())
        (self.state / "agent-handoff/refresh-recovery").unlink()

    def test_main_summary_keeps_verdict_and_omits_body(self):
        spec = importlib.util.spec_from_file_location("auto_close", HERE / "auto-tab-close.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        first = "result " + "long " * 60 + "failed"
        summary = module.short_report(first + "\nbody must stay in file\nTASK_CHECK: fail\n남은 것: retry", cap=None)
        self.assertIn(first, summary)
        self.assertIn("TASK_CHECK: fail", summary.splitlines()[0])
        self.assertNotIn("body must", summary)
        self.assertEqual(len(summary.splitlines()), 2)

        context = self.root / "check-context.json"
        context.write_text(json.dumps(dict(job="api#1209", first="worker finished",
                            summary="worker finished [TASK_RESULT: pass]\n남은 것: worker follow-up",
                            report="worker-report.md", entry="13:00", worker_address="workers/w1:p2",
                            kind="auto", kind_reason="independent check")))
        sent = []
        reply = "check failed\nprivate body\nTASK_CHECK: fail\n남은 것: 요유 몫: 없음 · 유요 몫: retry · 세션: 닫아도 됨"
        with patch.object(module.herdr_peers, "delegates_checks", return_value=True), \
             patch.object(module, "context_file", return_value=str(context)):
            self.assertTrue(module.route_main_report(reply, {}, "spawn-M-chk1", "main", {},
                            "check-report.md", datetime.now(), {}, lambda text: sent.append(text) or True))
        self.assertIn("worker finished [TASK_RESULT: pass]", sent[0])
        self.assertIn("남은 것: worker follow-up", sent[0])
        self.assertIn("check failed [TASK_CHECK: fail]", sent[0])
        self.assertIn("check-report.md", sent[0])
        self.assertIn("worker-report.md", sent[0])
        self.assertNotIn("private body", sent[0])


if __name__ == "__main__":
    unittest.main()
