#!/usr/bin/env python3
"""Exercise batch recovery with private state and controlled process boundaries."""
import json
import importlib.util
import os
import re
import signal
import shlex
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock


class BatchRecovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state = self.root / "state/pane-tell"
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("HERDR", "CODEX", "CLAUDE", "MUNICE", "PANE_TELL"))}
        self.env.update(HOME=str(self.root / "home"), XDG_STATE_HOME=str(self.root / "state"),
                        HERDR_BIN_PATH=str(self.root / "herdr"), FIXTURE=str(self.root),
                        PANE_TELL_POLL=".01", PANE_TELL_WAIT="5")
        source = Path(os.environ.get("PANE_TELL_TEST_SUBJECT", Path(__file__).with_name("pane-tell"))).read_text()
        marker = 'if [ "${1:-}" = --ordered ]; then'
        prefix, dispatch = source.split(marker)
        dispatch = marker + dispatch.split('if [ "${1:-}" = --box-ready ]; then')[0]
        (self.root / "functions").write_text(prefix)
        (self.root / "dispatch").write_text(dispatch)
        self.subject = self.root / "subject"
        self.subject.write_text('''#!/bin/bash
mkdir -p "$FIXTURE/started" "$FIXTURE/finished"
: > "$FIXTURE/started/$$"
trap ': > "$FIXTURE/finished/$$"' EXIT
if [ "${1:-}" = --deliver ] && [ -f "$FIXTURE/fail-start" ]; then
  echo 'fixture recovery startup failure' >&2
  exit 73
fi
if [ "${1:-}" = --deliver-owned ] && [ -f "$FIXTURE/spawn-descendant" ]; then
  /bin/bash -c 'trap "" TERM; echo "$$" > "$FIXTURE/descendant"; exec /bin/sleep 300' &
fi
source "$FIXTURE/functions"
definition=$(declare -f batch_event); eval "real_$definition"
batch_event() {
  real_batch_event "$@"
  [ "$2" != flush-exit ] || : > "$FIXTURE/finished/$$"
}
send() {
  printf '%s\\0' "$3" >> "$FIXTURE/prompts"
  printf '%s\\n' "${HERDR_SOCKET_PATH:-default}" >> "$FIXTURE/sockets"
  [ -z "${SEND_RETURN_DELAY:-}" ] || /bin/sleep "$SEND_RETURN_DELAY"
}
definition=$(declare -f register_receiver); eval "real_$definition"
register_receiver() {
  [ ! -f "$FIXTURE/fail-register" ] || return 73
  real_register_receiver "$@"
}
definition=$(declare -f batch_meta); eval "real_$definition"
batch_meta() {
  if [ "$1" = handoff ] && [ -f "$FIXTURE/fail-handoff" ]; then return 73; fi
  real_batch_meta "$@"
}
warn() { printf '%s\\n' "$1" >> "$FIXTURE/alerts"; }
if [ "${1:-}" = fixture-open ]; then
  ordered term_M relay w1:pM claude "$2" term_M sM '' '' w1:pM '👑 receiver' "$2"
  exit
fi
source "$FIXTURE/dispatch"
exit 92
''')
        (self.root / "herdr").write_text('''#!/bin/bash
case "$1 $2" in
 'pane get') printf '%s' '{"result":{"pane":{"agent_status":"idle"}}}' ;;
 'pane list') printf '%s' '{"result":{"panes":[{"pane_id":"w1:pM","terminal_id":"term_M","agent":"claude","agent_session":{"value":"sM"}}]}}' ;;
 *) exit 2 ;;
esac
''')
        (self.root / "herdr").chmod(0o755)
        bindir = self.root / "bin"
        bindir.mkdir()
        (self.root / "scheduler-finished").mkdir()
        # The scheduler exits before starting its child. No real process is killed.
        (bindir / "nohup").write_text('''#!/bin/bash
case " $* " in *' --flush '*)
  printf '%s\\n' "$*" >> "$FIXTURE/scheduled"
  : > "$FIXTURE/scheduler-finished/$$"
  exit 73;;
esac
exec /usr/bin/nohup "$@"
''')
        (bindir / "nohup").chmod(0o755)
        self.env["PATH"] = str(bindir) + ":" + self.env["PATH"]
        self.batch = self.state / "batch/term_M"
        self.killed_timers = set()

    def timer_alive(self, pid):
        result = subprocess.run(["/bin/ps", "-p", str(pid), "-o", "stat="],
                                capture_output=True, text=True, timeout=2)
        return result.returncode == 0 and result.stdout.strip() and not result.stdout.strip().startswith("Z")

    def tearDown(self):
        end = time.monotonic() + 7
        while time.monotonic() < end:
            started = list((self.root / "started").glob("*"))
            scheduled = []
            schedulers = []
            if (self.batch / "events").exists():
                events = (self.batch / "events").read_text()
                scheduled = re.findall(r"(?:recovery-spawn |delivery-scheduled )(\d+)",
                                       events)
                schedulers = re.findall(r"^\d+ \d+ scheduled (\d+)$", events, re.M)
            ids = [p.name for p in started] + scheduled
            def stopped(pid, directory):
                return ((self.root / directory / pid).exists()
                        or (pid in self.killed_timers and not self.timer_alive(pid)))
            if (all(stopped(pid, "finished") for pid in ids)
                    and all(stopped(pid, "scheduler-finished") for pid in schedulers)
                    and not list((self.state / "waiting").glob("*"))
                    and not list((self.state / "receivers").glob("*"))
                    and not list((self.state / "order/dependencies").glob("*"))):
                break
            time.sleep(.01)
        else:
            self.tmp._finalizer.detach()
            self.fail("private workers have not exited; retained " + str(self.root))
        self.tmp.cleanup()

    def call(self, *args, **env):
        return subprocess.run(["/bin/bash", str(self.subject), *args], env=dict(self.env, **env),
                              capture_output=True, text=True, timeout=7)

    def open(self, message="first"):
        result = self.call("fixture-open", message)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("held up to 120s", result.stdout)

    def due(self):
        path = self.batch / "open/.meta"
        metadata = json.loads(path.read_text())
        metadata["due"] = 0
        path.write_text(json.dumps(metadata))

    def prompts(self):
        path = self.root / "prompts"
        return path.read_bytes().split(b"\0")[:-1] if path.exists() else []

    def wait_prompt(self, expected):
        end = time.monotonic() + 7
        while self.prompts() != expected and time.monotonic() < end:
            time.sleep(.01)
        self.assertEqual(self.prompts(), expected)

    def test_normal_batch_metadata_and_explicit_flush(self):
        self.open()
        self.open("second")
        metadata = json.loads((self.batch / "open/.meta").read_text())
        self.assertEqual(metadata["due"] - metadata["opened"], 120)
        self.assertEqual(metadata["server"], "default")
        self.assertNotIn("first", json.dumps(metadata))
        result = self.call("--flush", str(self.batch), "term_M", PANE_TELL_BATCH="0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.prompts(), [b"first\n\nsecond"])
        events = (self.batch / "events").read_text()
        self.assertIn("flush-start", events)
        self.assertIn("flush-exit 0", events)

    def test_lost_scheduler_recovered_once_by_concurrent_ticks(self):
        self.open()
        self.due()
        commands = [["/bin/bash", str(self.subject), "--recover-batch"] for _ in range(4)]
        children = [subprocess.Popen(c, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for c in commands]
        for child in children:
            _, err = child.communicate(timeout=7)
            self.assertEqual(child.returncode, 0, err)
        self.wait_prompt([b"first"])
        result = self.call("--recover-batch")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.prompts(), [b"first"])

    def test_sigkilled_real_scheduler_is_recovered_once_by_watcher_tick(self):
        # Run the actual detached 120-second --flush scheduler, rather than
        # the default fixture's nohup exit73. Only this private group is killed.
        (self.root / "bin/nohup").write_text("#!/bin/bash\nexec /usr/bin/nohup \"$@\"\n")
        timer = None
        checkpoints = {}

        def owned_timer(pid):
            result = subprocess.run(["/bin/ps", "-p", str(pid), "-o", "command="],
                                    capture_output=True, text=True, timeout=2)
            return (result.returncode == 0 and str(self.subject) in result.stdout
                    and "--flush" in result.stdout and str(self.batch) in result.stdout
                    and os.getpgid(pid) == pid)

        def kill_timer():
            if timer is not None and self.timer_alive(timer):
                self.assertTrue(owned_timer(timer), "refusing to signal an unowned scheduler")
                os.killpg(timer, signal.SIGKILL)
                self.killed_timers.add(str(timer))
                checkpoints["killed_at"] = time.time()

        try:
            self.open("synthetic killed scheduler")
            end = time.monotonic() + 7
            while time.monotonic() < end:
                events = (self.batch / "events").read_text()
                scheduled = re.findall(r"^\d+ \d+ scheduled (\d+)$", events, re.M)
                if scheduled:
                    timer = int(scheduled[0])
                    if f" {timer} flush-start " in events and owned_timer(timer):
                        group = subprocess.run(["/bin/ps", "-g", str(timer), "-o", "command="],
                                               capture_output=True, text=True, timeout=2)
                        if "sleep 120" in group.stdout:
                            checkpoints["started_at"] = time.time()
                            break
                time.sleep(.01)
            else:
                self.fail("actual private 120-second scheduler did not start")
            self.assertEqual(self.prompts(), [])
            self.assertEqual(json.loads((self.batch / "open/.meta").read_text())["due"]
                             - json.loads((self.batch / "open/.meta").read_text())["opened"], 120)
            kill_timer()
            end = time.monotonic() + 7
            while self.timer_alive(timer) and time.monotonic() < end:
                time.sleep(.01)
            self.assertFalse(self.timer_alive(timer))
            while time.monotonic() < end:
                group = subprocess.run(["/bin/ps", "-g", str(timer), "-o", "stat="],
                                       capture_output=True, text=True, timeout=2)
                if not any(row.strip() and not row.strip().startswith("Z")
                           for row in group.stdout.splitlines()):
                    break
                time.sleep(.01)
            else:
                self.fail("SIGKILL left a live member in the private timer group")
            checkpoints["group_stopped_at"] = time.time()
            self.assertNotIn(f" {timer} flush-exit ", (self.batch / "events").read_text())
            self.assertTrue(list((self.batch / "open").glob("[0-9]*")))
            self.due()  # Backdate private metadata only; keep production delay unchanged.

            source = Path(__file__).with_name("pane-relay-watch.py")
            spec = importlib.util.spec_from_file_location("watch_killed_scheduler", source)
            watcher = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(watcher)
            # Keep the watcher's baseline helpers inside the same private
            # fixture when replacing HERE for the recovery subprocess.
            for name in ("main-user-relay.py", "main-alert-inbox.py"):
                (self.root / name).write_bytes((source.parent / name).read_bytes())
            # Resolve the real recovery subprocess to our shell/fake-herdr
            # fixture. Wake is outside this test's claim and never sees a pane.
            (self.root / "pane-tell").symlink_to(self.subject)
            with mock.patch.object(watcher, "HERE", str(self.root)), \
                    mock.patch.object(watcher, "wake_main", return_value=False):
                watcher.tick(self.env, time.time())
                self.wait_prompt([b"synthetic killed scheduler"])
                checkpoints["prompt_at"] = time.time()
                # Delivery ACK removes queue/receiver/dependency state. Wait
                # for that actual acknowledgement, then retry the watcher.
                end = time.monotonic() + 7
                while time.monotonic() < end:
                    if (not list((self.state / "waiting").glob("*"))
                            and not list((self.state / "receivers").glob("*"))
                            and not list((self.state / "order/dependencies").glob("*"))):
                        break
                    time.sleep(.01)
                else:
                    self.fail("recovered delivery did not acknowledge queue cleanup")
                checkpoints["cleanup_ack_at"] = time.time()
                watcher.tick(self.env, time.time())
            events = (self.batch / "events").read_text()
            self.assertIn("recovery-spawn", events)
            self.assertIn("recovery-exit 0", events)
            self.assertIn("delivered batch-", (self.state / "log").read_text())
            self.assertEqual(self.prompts(), [b"synthetic killed scheduler"])
            print("private scheduler recovery " + json.dumps(dict(
                checkpoints, pid=timer, pgid=timer, prompt_count=len(self.prompts()),
                live_group_members=0, queue_cleanup_ack=True), sort_keys=True), flush=True)
        finally:
            kill_timer()

    def test_not_due_is_retained(self):
        self.open()
        self.assertEqual(self.call("--recover-batch").returncode, 0)
        self.assertTrue((self.batch / "open").is_dir())
        self.assertEqual(self.prompts(), [])

    def test_legacy_ambiguous_batch_is_retained_and_reported(self):
        (self.batch / "open").mkdir(parents=True)
        (self.batch / "open/0001").write_text("private")
        result = self.call("--recover-batch")
        self.assertEqual(result.returncode, 2)
        self.assertTrue((self.batch / "open/0001").exists())
        self.assertEqual(self.prompts(), [])

    def test_invalid_metadata_is_fail_closed(self):
        self.open()
        (self.batch / "open/.meta").write_text('{"version":1}')
        self.assertEqual(self.call("--recover-batch").returncode, 2)
        self.assertTrue((self.batch / "open").is_dir())
        self.assertEqual(self.prompts(), [])

    def test_no_batches_is_noop(self):
        self.assertEqual(self.call("--recover-batch").returncode, 0)

    def test_general_waiting_recovers_without_batch_and_preserves_order(self):
        (self.state.parent / "orchestrator").mkdir(parents=True)
        (self.state.parent / "orchestrator/main").write_text("term_M\n")
        waiting = self.state / "waiting"
        receivers = self.state / "receivers"
        dependencies = self.state / "order/dependencies"
        for directory in (waiting, receivers, dependencies):
            directory.mkdir(parents=True, exist_ok=True)
        for name, text in (("100.first", "first"), ("101.second", "second")):
            payload = waiting / name
            payload.write_text(text)
            os.utime(payload, (1, 1))
            (receivers / name).write_text("term_M\ndefault\n")
        (dependencies / "100.first").write_text("")
        (dependencies / "101.second").write_text(str(waiting / "100.first") + "\n")
        self.assertEqual(self.call("--recover-batch", SEND_RETURN_DELAY=".1").returncode, 0)
        self.wait_prompt([b"first", b"second"])
        # A prompt is visible before send returns and delivery removes its
        # durable queue files. Check the acknowledgement, not that earlier event.
        directories = (waiting, receivers, dependencies)
        end = time.monotonic() + 7
        while any(list(path.iterdir()) for path in directories) and time.monotonic() < end:
            time.sleep(.01)
        for path in directories:
            self.assertEqual(list(path.iterdir()), [], str(path))

    def private_ordered_pair(self):
        for directory in ("waiting", "receivers", "delivery-routes", "order/dependencies"):
            (self.state / directory).mkdir(parents=True, exist_ok=True)
        first, second = [self.state / "waiting" / name for name in ("100.first", "101.second")]
        for payload, text in ((first, "first"), (second, "second")):
            payload.write_text(text)
            os.utime(payload, (1, 1))
            (self.state / "receivers" / payload.name).write_text("term_M\ndefault\n")
            (self.state / "delivery-routes" / payload.name).write_text("sM\n\n\n")
            (self.state / "order/dependencies" / payload.name).write_text(
                str(first) + "\n" if payload == second else "")
        return first, second

    def test_owner_sigkill_is_observed_and_originals_recover_in_order(self):
        first, second = self.private_ordered_pair()
        (self.root / "spawn-descendant").touch()
        worker = subprocess.Popen(["/bin/bash", str(self.subject), "--deliver", str(second),
                                   "term_M", "sM", "", ""], env=dict(self.env,
                                   HERDR_PANE_ID="w9:p7", HERDR_SESSION="workers",
                                   PANE_TELL_SOURCE_SESSION="source-session"),
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  start_new_session=True)
        try:
            end = time.monotonic() + 3
            match = None
            while time.monotonic() < end:
                log = self.state / "log"
                text = log.read_text() if log.exists() else ""
                match = re.search(r"worker-start 101.second pid=(\d+)", text)
                if match:
                    break
                time.sleep(.01)
            self.assertIsNotNone(match, text)
            owner = int(match[1])
            self.assertEqual(os.getpgid(owner), worker.pid)
            end = time.monotonic() + 2
            while not (self.root / "descendant").exists() and time.monotonic() < end:
                time.sleep(.01)
            descendant = int((self.root / "descendant").read_text())
            delivery = int(re.search(r"worker-start 101.second .*delivery=(\d+)", text)[1])
            self.assertEqual(os.getpgid(descendant), delivery)
            self.assertEqual(self.prompts(), [])
            os.kill(owner, signal.SIGKILL)
            self.assertNotEqual(worker.wait(timeout=3), 0)
            self.assertEqual(second.read_text(), "second")
            self.assertTrue((self.state / "receivers" / second.name).exists())
            self.assertEqual((self.state / "order/dependencies" / second.name).read_text(), str(first) + "\n")
            text = (self.state / "log").read_text()
            self.assertRegex(text, r"owner-exit 101.second .*status=-9 signal=SIGKILL sender=unknown")
            owner_line = next(line for line in text.splitlines() if "owner-exit 101.second" in line)
            self.assertIn('source_pane="w9:p7"', owner_line)
            self.assertIn('source_server="workers"', owner_line)
            self.assertIn('source_session="source-session"', owner_line)
            self.assertRegex(owner_line, r"parent=\d+")
            self.assertRegex(owner_line, r"observed_epoch=\d+\.\d+")
            delivery_line = next(line for line in text.splitlines()
                                 if "worker-exit 101.second" in line and "observed_by=" in line)
            self.assertIn("pid=" + str(delivery), delivery_line)
            self.assertRegex(delivery_line, r"signal=(SIGTERM|SIGKILL)")
            self.assertRegex(text, r"supervisor-signal 101.second .*signal=SIGTERM actor=\d+")
            self.assertIn("reason=delivery-cleanup action=attempt", text)
            self.assertFalse(self.timer_alive(descendant), "delivery descendant survived owner death")
        finally:
            (self.root / "spawn-descendant").unlink()
            # Assertion failures must also clean the private delivery group.
            rows = subprocess.check_output(["/bin/ps", "-axo", "pid=,command="], text=True)
            for row in rows.splitlines():
                fields = row.strip().split(None, 1)
                if len(fields) != 2: continue
                try:
                    args = shlex.split(fields[1])
                    pid = int(fields[0])
                    if (args[1:4] == [str(self.subject), "--deliver-owned", str(second)]
                            and os.getpgid(pid) == pid):
                        os.killpg(pid, signal.SIGKILL)
                        self.killed_timers.add(str(pid))
                except (ValueError, ProcessLookupError):
                    pass
            if worker.poll() is None:
                try:
                    worker.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    worker.terminate()
                    worker.wait(timeout=3)
            # Exercise the existing scan; never discard or reorder payloads,
            # even when an assertion above fails on the pre-fix implementation.
            self.assertEqual(self.call("--recover-batch").returncode, 0)
            self.wait_prompt([b"first", b"second"])

    def test_general_recovery_preserves_worker_startup_stderr(self):
        self.private_ordered_pair()
        failure = self.root / "fail-start"
        failure.touch()
        try:
            self.assertEqual(self.call("--recover-batch").returncode, 0)
            errors = self.state / "worker-errors.log"
            end = time.monotonic() + 2
            while time.monotonic() < end:
                if errors.exists() and "fixture recovery startup failure" in errors.read_text():
                    break
                time.sleep(.01)
            self.assertTrue(errors.exists(), "recovery discarded worker stderr")
            self.assertIn("fixture recovery startup failure", errors.read_text())
            self.assertEqual(len(list((self.state / "waiting").iterdir())), 2)
        finally:
            failure.unlink()
            self.assertEqual(self.call("--recover-batch").returncode, 0)
            self.wait_prompt([b"first", b"second"])

    def test_general_recovery_retains_original_sender_context(self):
        first, second = self.private_ordered_pair()
        try:
            command = ['source "$FIXTURE/functions"; register_receiver "$1" term_M sM "" ""',
                       "fixture", str(first)]
            result = subprocess.run(["/bin/bash", "-c", *command], env=dict(self.env,
                PANE_TELL_SOURCE_PANE="w9:p8", PANE_TELL_SOURCE_SERVER="workers",
                PANE_TELL_SOURCE_SESSION="original-session", PANE_TELL_SOURCE_PARENT="123"),
                capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            origin = self.state / "delivery-origins" / first.name
            self.assertTrue(origin.exists(), "queue did not retain sender context")
            self.assertEqual(json.loads(origin.read_text())["session"], "original-session")
        finally:
            self.assertEqual(self.call("--recover-batch").returncode, 0)
            self.wait_prompt([b"first", b"second"])
        end = time.monotonic() + 3
        while time.monotonic() < end:
            lines = (self.state / "log").read_text().splitlines()
            line = next((line for line in lines if "delivery-exit 100.first" in line), "")
            if line: break
            time.sleep(.01)
        self.assertTrue(line, "delivery exit was not recorded")
        self.assertIn('source_pane="w9:p8"', line)
        self.assertIn('source_session="original-session"', line)
        self.assertIn('source_parent="123"', line)

    def test_general_recovery_keeps_original_session_binding(self):
        for name in ("waiting", "receivers", "delivery-routes", "undelivered", "order/dependencies"):
            (self.state / name).mkdir(parents=True, exist_ok=True)
        payload = self.state / "waiting/100.old-session"
        payload.write_text("must not enter a replacement session")
        os.utime(payload, (1, 1))
        (self.state / "receivers" / payload.name).write_text("term_M\ndefault\n")
        (self.state / "delivery-routes" / payload.name).write_text("old-session\n\n\n")
        (self.state / "order/dependencies" / payload.name).write_text("")
        self.assertEqual(self.call("--recover-batch").returncode, 0)
        end = time.monotonic() + 7
        while payload.exists() and time.monotonic() < end:
            time.sleep(.01)
        self.assertTrue((self.state / "undelivered" / payload.name).exists())
        self.assertEqual(self.prompts(), [])

    def test_general_recovery_retains_unprovable_session_or_order(self):
        for name in ("waiting", "receivers", "delivery-routes", "order/dependencies"):
            (self.state / name).mkdir(parents=True, exist_ok=True)
        for name, missing in (("100.legacy", "session"), ("101.unordered", "order")):
            with self.subTest(missing=missing):
                payload = self.state / "waiting" / name
                receiver = self.state / "receivers" / name
                dependency = self.state / "order/dependencies" / name
                payload.write_text("retain until routing and order can be proved")
                os.utime(payload, (1, 1))
                receiver.write_text("term_M\ndefault\n")
                if missing == "session": dependency.write_text("")
                else: (self.state / "delivery-routes" / name).write_text("sM\n\n\n")
                self.assertEqual(self.call("--recover-batch").returncode, 0)
                self.assertTrue(payload.exists())
                self.assertEqual(self.prompts(), [])
                payload.unlink()
                receiver.unlink()
                dependency.unlink(missing_ok=True)

    def test_scheduler_failure_records_exit_and_retains_original(self):
        self.open()
        sleeper = self.root / "bin/sleep"
        sleeper.write_text("#!/bin/bash\nexit 73\n")
        sleeper.chmod(0o755)
        result = self.call("--flush", str(self.batch), "term_M")
        self.assertEqual(result.returncode, 73, result.stderr)
        self.assertIn("flush-exit 73", (self.batch / "events").read_text())
        self.assertTrue(list((self.batch / "open").glob("[0-9]*")))
        sleeper.unlink()
        self.due()
        self.assertEqual(self.call("--recover-batch").returncode, 0)
        self.wait_prompt([b"first"])

    def test_failed_queue_claim_is_recoverable_without_duplicate_enqueue(self):
        self.open()
        self.due()
        mover = self.root / "bin/mv"
        mover.write_text('''#!/bin/bash
case "$1:$2" in */.joined.*:*/waiting/*) exit 73;; esac
exec /bin/mv "$@"
''')
        mover.chmod(0o755)
        result = self.call("--recover-one", str(self.batch), "term_M")
        self.assertEqual(result.returncode, 2)
        self.assertTrue(list((self.batch / "claimed").glob("[0-9]*")))
        self.assertEqual(self.prompts(), [])
        mover.unlink()
        self.assertEqual(self.call("--recover-batch").returncode, 0)
        self.wait_prompt([b"first"])
        self.assertEqual(len(list((self.batch / "jobs").iterdir())), 1)

    def test_stale_scheduler_generation_does_not_take_new_batch(self):
        self.open()
        old = json.loads((self.batch / "open/.meta").read_text())["generation"]
        self.assertEqual(self.call("--flush", str(self.batch), "term_M", PANE_TELL_BATCH="0").returncode, 0)
        self.open("new")
        result = self.call("--flush", str(self.batch), "term_M", old, PANE_TELL_BATCH="0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.prompts(), [b"first"])
        self.assertTrue((self.batch / "open").is_dir())

    def test_wrong_server_cannot_append_or_flush_recorded_batch(self):
        self.open()
        socket = "/private/fixture/sessions/workers/herdr.sock"
        result = self.call("fixture-open", "other", HERDR_SOCKET_PATH=socket)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.prompts(), [])
        result = self.call("--flush", str(self.batch), "term_M", PANE_TELL_BATCH="0", HERDR_SOCKET_PATH=socket)
        self.assertEqual(result.returncode, 2)
        self.assertTrue(list((self.batch / "claimed").glob("[0-9]*")))
        self.assertEqual(self.prompts(), [])

    def test_registration_failure_reuses_durable_waiting_payload(self):
        self.open()
        self.due()
        fault = self.root / "fail-register"
        fault.touch()
        result = self.call("--recover-one", str(self.batch), "term_M")
        self.assertEqual(result.returncode, 2)
        queued = list((self.state / "waiting").iterdir())
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0].read_text(), "first")
        self.assertTrue(list((self.batch / "claimed").glob("[0-9]*")))
        fault.unlink()
        self.assertEqual(self.call("--recover-batch").returncode, 0)
        self.wait_prompt([b"first"])
        self.assertEqual(len(list((self.batch / "jobs").iterdir())), 1)

    def test_recovery_restores_recorded_workers_route(self):
        socket = "/private/fixture/sessions/workers/herdr.sock"
        result = self.call("fixture-open", "workers", HERDR_SOCKET_PATH=socket)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.due()
        self.assertEqual(self.call("--recover-batch").returncode, 0)
        self.wait_prompt([b"workers"])
        self.assertEqual((self.root / "sockets").read_text().strip(), socket)

    def test_default_scheduler_waits_120_before_flushing(self):
        self.open()
        sleeper = self.root / "bin/sleep"
        sleeper.write_text('''#!/bin/bash
if [ "$1" = 120 ]; then
  printf '%s' "$1" > "$FIXTURE/sleep-entered"
  while [ ! -f "$FIXTURE/release-sleep" ]; do /bin/sleep .01; done
  exit 0
fi
exec /bin/sleep "$@"
''')
        sleeper.chmod(0o755)
        child = subprocess.Popen(["/bin/bash", str(self.subject), "--flush", str(self.batch), "term_M"],
                                 env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            end = time.monotonic() + 7
            while not (self.root / "sleep-entered").exists() and time.monotonic() < end:
                time.sleep(.01)
            self.assertEqual((self.root / "sleep-entered").read_text(), "120")
            self.assertEqual(self.prompts(), [])
        finally:
            (self.root / "release-sleep").touch()
            _, err = child.communicate(timeout=7)
        self.assertEqual(child.returncode, 0, err)
        self.assertEqual(self.prompts(), [b"first"])

    def test_handoff_metadata_failure_keeps_claim_and_queue_for_retry(self):
        self.open()
        self.due()
        (self.root / "fail-handoff").touch()
        result = self.call("--recover-one", str(self.batch), "term_M")
        self.assertEqual(result.returncode, 2)
        self.assertTrue(list((self.batch / "claimed").glob("[0-9]*")))
        self.assertEqual(len(list((self.state / "waiting").iterdir())), 1)
        self.assertEqual(self.prompts(), [])
        (self.root / "fail-handoff").unlink()
        self.assertEqual(self.call("--recover-batch").returncode, 0)
        self.wait_prompt([b"first"])
        self.assertEqual(len(list((self.batch / "jobs").iterdir())), 1)

    def test_committed_handoff_is_not_replayed_from_retained_claim(self):
        self.open()
        self.assertEqual(self.call("--flush", str(self.batch), "term_M", PANE_TELL_BATCH="0").returncode, 0)
        job = next((self.batch / "jobs").iterdir())
        claim = self.batch / "claimed"
        claim.mkdir()
        (claim / ".meta").write_text(job.read_text())
        (claim / "0001").write_text("first")
        result = self.call("--recover-one", str(self.batch), "term_M")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.prompts(), [b"first"])
        self.assertFalse(claim.exists())
        self.assertEqual(self.call("--recover-batch").returncode, 0)
        self.assertTrue((self.batch / "finished" / job.name).exists())

    def test_urgent_reuses_partial_batch_handoff(self):
        self.open()
        self.due()
        (self.root / "fail-handoff").touch()
        self.assertEqual(self.call("--recover-one", str(self.batch), "term_M").returncode, 2)
        (self.root / "fail-handoff").unlink()
        metadata = json.loads((self.batch / "claimed/.meta").read_text())
        (self.batch / "jobs").mkdir()
        (self.batch / "jobs" / metadata["generation"]).write_text(json.dumps(metadata))
        result = self.call("fixture-open", "prod 허용")
        self.assertEqual(result.returncode, 0, result.stderr)
        # An urgent relay must reuse the existing batch handoff, then queue its
        # grant behind it. It must not create a third copy of the held message.
        try:
            self.assertLessEqual(len(list((self.state / "waiting").iterdir())), 2)
        finally:
            self.call("--recover-batch")
        self.wait_prompt([b"first", "prod 허용".encode()])

    def test_unreadable_metadata_is_retained(self):
        self.open()
        metadata = self.batch / "open/.meta"
        metadata.chmod(0)
        try:
            with self.assertRaises(PermissionError):
                metadata.read_text()
            result = self.call("--recover-batch")
            self.assertEqual(result.returncode, 2)
            self.assertEqual(self.prompts(), [])
            self.assertTrue(list((self.batch / "open").glob("[0-9]*")))
        finally:
            metadata.chmod(0o600)

    def test_retained_claim_and_new_open_keep_arrival_order(self):
        self.open()
        self.due()
        fault = self.root / "fail-register"
        fault.touch()
        self.assertEqual(self.call("--recover-one", str(self.batch), "term_M").returncode, 2)
        fault.unlink()
        self.open("second")
        result = self.call("fixture-open", "prod 허용")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.wait_prompt([b"first", b"second", "prod 허용".encode()])

    def test_fifo_metadata_is_rejected_without_waiting_for_a_writer(self):
        self.open()
        path = self.batch / "open/.meta"
        payload = path.read_bytes()
        path.unlink()
        os.mkfifo(path)
        stopped = threading.Event()

        def writer():
            # Also lets the old blocking reader finish safely during RED. A
            # correct reader rejects the FIFO before attempting to read it.
            while not stopped.is_set():
                try:
                    fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
                except OSError:
                    stopped.wait(.01)
                    continue
                try:
                    try:
                        os.write(fd, payload)
                    except BrokenPipeError:
                        pass  # the bounded reader already rejected the FIFO
                finally:
                    os.close(fd)
                return

        helper = threading.Thread(target=writer)
        helper.start()
        try:
            result = self.call("--recover-batch")
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertEqual(self.prompts(), [])
        finally:
            stopped.set()
            helper.join(timeout=1)
        self.assertFalse(helper.is_alive())
        result = self.call("--flush", str(self.batch), "term_M", PANE_TELL_BATCH="0")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(self.prompts(), [])


if __name__ == "__main__":
    unittest.main()
