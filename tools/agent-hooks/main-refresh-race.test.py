#!/usr/bin/env python3
"""Offline boundary test: real refresh + relay queues; only herdr/agent are fixtures."""
import importlib.util
import fcntl
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent


def until(predicate, timeout=60):
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
        # The fixture must not start an extra Python interpreter for every CLI
        # query inside the watcher's real deadlines, especially under load.
        fake.write_text('''#!/bin/sh
r=$FIXTURE
sid=$(cat "$r/session")
main() {
    jq -cn --arg sid "$sid" --arg status "$(cat "$r/status")" \\
      '{pane_id:"w1:p1",terminal_id:"main-terminal",agent:"claude",agent_session:{value:$sid},agent_status:$status,label:"main",scroll:{offset_from_bottom:0}}'
}
sender='{"pane_id":"w1:p9","terminal_id":"sender-terminal","agent":"codex","agent_session":{"value":"sender"},"label":"worker"}'
append_user() {
    jq -cn --arg text "$text" '{type:"user",message:{content:$text}}' >> "$r/old-session.jsonl"
}
case "$1 $2" in
    'pane list') printf '{"result":{"panes":[%s,%s]}}\\n' "$(main)" "$sender" ;;
    'pane get')
        case "$3" in
            w1:p1)
                if [ -e "$r/get-fails" ]; then
                    rm "$r/get-fails"
                    touch "$r/get-failed"
                    exit 1
                fi
                row=$(main) ;;
            w1:p9) row=$sender ;;
            *) exit 1 ;;
        esac
        printf '{"result":{"pane":%s}}\\n' "$row" ;;
    'pane read')
        draft=
        [ ! -e "$r/full" ] || draft='typed draft'
        printf '%s\\n' '────────────────────────────────────────' "❯ $draft" '────────────────────────────────────────' '  footer' ;;
    'tab list') printf '%s\\n' '{"result":{"tabs":[]}}' ;;
    'session list') printf '%s\\n' '{"sessions":[{"name":"default","running":true,"socket_path":"/default/herdr.sock"}]}' ;;
    'agent prompt')
        [ "$3" = w1:p1 ] || exit 0
        text=$4
        case "$text" in *'[Slack #0-yoyooyo 요유]'*)
            if [ -e "$r/pause-priority" ]; then
                touch "$r/priority-inflight"
                while [ ! -e "$r/priority-continue" ]; do sleep .02; done
            fi ;;
        esac
        jq -cn --arg sid "$sid" --arg text "$text" '[$sid,$text]' >> "$r/prompts"
        if [ "$text" = /clear ]; then
            touch "$r/clearing"
            [ ! -e "$r/return-before-clear" ] || exit 0
            while [ ! -e "$r/clear-continue" ]; do sleep .02; done
            printf '%s' new-session > "$r/session"
            printf '%s' idle > "$r/status"
        else
            append_user
            case "$text" in *'[main-refresh]'*)
                touch "$r/requested"
                if [ -e "$r/pause-retry" ] && [ -e "$r/no-handoff" ]; then
                    touch "$r/retry-inflight"
                    while [ ! -e "$r/retry-continue" ]; do sleep .02; done
                fi
                if [ ! -e "$r/no-handoff" ]; then
                    sleep .05
                    printf '%s\\nhandoff' "${text##* }" > "$r/state/agent-handoff/w1:p1.md"
                    if [ -e "$r/snapshot-fails" ]; then
                        printf '%s' 'not a directory' > "$r/state/agent-handoff/refresh-recovery"
                    fi
                fi
                [ ! -e "$r/hold-after-request" ] || printf '%s' working > "$r/status" ;;
            esac
        fi ;;
    'agent send-keys') printf '%s\\n' 'must preserve typed draft' >&2; exit 1 ;;
    *) exit 2 ;;
esac
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
        (self.root / "priority-continue").touch()
        (self.root / "retry-continue").touch()
        (self.root / "expire-deadline").touch()
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

    def tell(self, text, raw=False, cross=False, external=False):
        args = [str(HERE / "pane-tell"), *(["--raw"] if raw else []), "default/w1:p1" if cross else "w1:p1", text]
        env = {**self.env, "HERDR_SOCKET_PATH": "/sessions/workers/herdr.sock"} if cross else self.env
        if external:
            env = {key: value for key, value in env.items() if key != "HERDR_PANE_ID"}
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
        message = "[Slack #0-yoyooyo 요유] stop"
        self.assertIn("queued", self.tell(message, cross=True))
        self.assertFalse(any(text.endswith(message) for _, text in self.prompts()))
        (self.root / "clear-continue").touch()
        self.assertEqual(self.proc.wait(timeout=10), 0)
        until(lambda: any(text.endswith(message) for _, text in self.prompts()))
        received = [(sid, text) for sid, text in self.prompts() if text.endswith(message)]
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0][0], "new-session")

    def test_external_words_wait_for_new_session_without_recovery(self):
        (self.root / "status").write_text("idle")
        self.start()
        until(lambda: (self.root / "clearing").exists())
        message = "[Slack #0-yoyooyo 요유] stop"
        result = self.tell(message, external=True)
        self.assertIn("queued", result)
        self.assertFalse(any(text.endswith(message) for _, text in self.prompts()))
        (self.root / "clear-continue").touch()
        self.assertEqual(self.proc.wait(timeout=10), 0)
        until(lambda: any(text.endswith(message) for _, text in self.prompts()))
        self.assertEqual([sid for sid, text in self.prompts() if text.endswith(message)],
                         ["new-session"])
        self.assertFalse(list((self.state / "agent-handoff/refresh-recovery").glob("*/active")))

    def test_inflight_priority_finishes_before_prepare_and_aborted_record_is_completed(self):
        (self.root / "hold-after-request").touch()
        (self.root / "pause-priority").touch()
        (self.root / "pause-retry").touch()
        (self.root / "status").write_text("idle")
        self.start()
        until(lambda: (self.state / "agent-handoff/w1:p1.md").exists())
        message = "[Slack #0-yoyooyo 요유] stop"
        env = {key: value for key, value in self.env.items() if key != "HERDR_PANE_ID"}
        sender = subprocess.Popen([str(HERE / "pane-tell"), "w1:p1", message], env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            until(lambda: (self.root / "priority-inflight").exists())
            (self.root / "no-handoff").touch()
            (self.root / "status").write_text("idle")
            lock_path = str(self.state / "pane-tell/sending/w1:p1.lock")
            def prepare_waiting_for_send():
                rows = subprocess.check_output(["/bin/ps", "-axo", "args="], text=True)
                return any(lock_path in row and "main-refresh-recovery.py" in row
                           for row in rows.splitlines())
            until(prepare_waiting_for_send)
            self.assertFalse((self.root / "clearing").exists())
            self.assertFalse(list((self.state / "agent-handoff/refresh-recovery").glob("*/handoff.md")))
        finally:
            (self.root / "priority-continue").touch()
            sender.communicate(timeout=10)
        self.assertEqual(sender.returncode, 0)
        until(lambda: list((self.state / "agent-handoff/refresh-recovery").glob("*/completed")))
        # Completion is the abort boundary, not the end of --now: it may
        # perform another readiness pass and handoff. Release that separate
        # attempt only when the fixture reaches it (or the run budget ends).
        until(lambda: (self.root / "retry-inflight").exists() or self.proc.poll() is not None)
        (self.root / "retry-continue").touch()
        self.assertEqual(self.proc.wait(timeout=10), 0)
        self.assertNotIn("/clear", [text for _, text in self.prompts()])
        self.assertEqual([sid for sid, text in self.prompts() if text.endswith(message)],
                         ["old-session"])
        self.assertFalse(list((self.state / "agent-handoff/refresh-recovery").glob("*/active")))

    def test_abort_after_prepare_reholds_delivery_for_second_handoff(self):
        (self.root / "hold-after-request").touch()
        (self.root / "pause-priority").touch()
        (self.root / "clear-continue").touch()
        (self.root / "status").write_text("idle")
        notifier = self.root / "notify"
        notifier.write_text('#!/bin/sh\n: > "$FIXTURE/error-notice"\n')
        notifier.chmod(0o755)
        self.env["HERDR_ERROR_NOTIFY"] = str(notifier)
        self.start()
        until(lambda: (self.state / "agent-handoff/w1:p1.md").exists())
        message = "[Slack #0-yoyooyo 요유] stop"
        env = {key: value for key, value in self.env.items() if key != "HERDR_PANE_ID"}
        sender = subprocess.Popen([str(HERE / "pane-tell"), "w1:p1", message], env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            until(lambda: (self.root / "priority-inflight").exists())
            (self.root / "status").write_text("idle")
            lock_path = str(self.state / "pane-tell/sending/w1:p1.lock")
            def prepare_waiting_for_send():
                rows = subprocess.check_output(["/bin/ps", "-axo", "args="], text=True)
                return any(lock_path in row and "main-refresh-recovery.py" in row
                           for row in rows.splitlines())
            until(prepare_waiting_for_send)
            (self.root / "hold-after-request").unlink()
        finally:
            (self.root / "priority-continue").touch()
            sender.communicate(timeout=10)
        self.assertEqual(sender.returncode, 0)
        self.assertEqual(self.proc.wait(timeout=30), 0)
        prompts = [text for _, text in self.prompts()]
        self.assertEqual(sum("[main-refresh]" in text for text in prompts), 2)
        self.assertEqual([sid for sid, text in self.prompts() if text.endswith(message)],
                         ["old-session"])
        self.assertEqual(prompts.count("/clear"), 1)
        self.assertFalse((self.root / "error-notice").exists())
        self.assertIn("새로 시작함", (self.state / "orchestrator/refresh.log").read_text())

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
        # Exercise expiry after /clear was accepted, not host startup speed.
        # Keep the real eight-second timer; hold only its completion signal
        # until the test has reached the recovery boundary under contention.
        bindir = self.root / "bin"
        bindir.mkdir(exist_ok=True)
        timer = bindir / "sleep"
        timer.write_text(
            '#!/bin/sh\n'
            'if [ "$1" = "$MAIN_REFRESH_DEADLINE" ]; then\n'
            '  /bin/sleep "$1" & timer=$!\n'
            '  trap \'if [ -n "$timer" ]; then kill "$timer" 2>/dev/null; wait "$timer" 2>/dev/null; fi; exit 0\' HUP INT TERM\n'
            '  wait "$timer"; timer=\n'
            '  while [ ! -e "$FIXTURE/expire-deadline" ]; do /bin/sleep .02; done\n'
            'else\n'
            '  exec /bin/sleep "$@"\n'
            'fi\n')
        timer.chmod(0o755)
        self.env["PATH"] = str(bindir) + os.pathsep + self.env["PATH"]
        self.start()
        until(lambda: (self.root / "clearing").exists())
        self.tell("after-accepted-clear", raw=True)
        self.assertIsNone(self.proc.poll(), "watcher must await the expiry signal")
        (self.root / "expire-deadline").touch()
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

    def test_refresh_fixture_does_not_need_another_python_startup(self):
        # The real watcher has a bounded workflow. Loading another Python
        # interpreter for each fake CLI call must not consume that budget.
        bindir = self.root / "bin"
        bindir.mkdir()
        python = bindir / "python3"
        python.write_text(
            '#!/bin/sh\n'
            'if [ "$1" = "$HERDR_BIN_PATH" ]; then sleep 3; fi\n'
            f'exec "{sys.executable}" "$@"\n')
        python.chmod(0o755)
        self.env["PATH"] = str(bindir) + os.pathsep + self.env["PATH"]
        self.test_clear_confirmation_timeout_recovers_handoff_and_relays()

    def test_dead_clear_owner_expires_and_record_failure_retains_queue(self):
        (self.root / "return-before-clear").touch()
        (self.root / "status").write_text("idle")
        self.env["MAIN_REFRESH_DEADLINE"] = "90"
        self.start()
        until(lambda: (self.root / "clearing").exists(), timeout=95)
        gate = json.loads((self.state / "orchestrator/refresh-delivery.json").read_text())
        self.proc.kill()
        self.proc.wait(timeout=5)
        gate["expires_at"] = time.time() - 1
        (self.state / "orchestrator/refresh-delivery.json").write_text(json.dumps(gate))
        self.tell("owner-died", raw=True)
        until(lambda: any(text == "owner-died" for _, text in self.prompts()), timeout=30)
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
        until(lambda: any(text == "must-retain-on-record-failure" for _, text in self.prompts()), timeout=30)
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
