#!/usr/bin/env python3
"""Exercise delivery ordering with isolated herdr and controlled OS boundaries."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

SUBJECT = Path(os.environ.get("PANE_TELL_TEST_SUBJECT", Path(__file__).with_name("pane-tell")))


class DeliveryOrder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.children = []
        self.legacy_payloads = []
        self.state = self.root / "state/pane-tell"
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("HERDR", "CODEX", "CLAUDE", "MUNICE", "PANE_TELL"))}
        self.env.update(HOME=str(self.root / "home"), XDG_STATE_HOME=str(self.root / "state"),
                        HERDR_BIN_PATH=str(self.root / "herdr"), HERDR_PANE_ID="w1:p9",
                        PANE_TELL_BATCH="0", PANE_TELL_SETTLE="0", PANE_TELL_POLL="0.02",
                        PANE_TELL_WAIT="5", PANE_TELL_ALERT="600", FIXTURE=str(self.root),
                        HERDR_ERROR_NOTIFY=str(self.root / "error-notify"), FIXTURE_PARENT=str(os.getpid()))
        (self.root / "error-notify").write_text('''#!/bin/bash
printf '%s\\n' "$2" >> "$FIXTURE/alerts"
''')
        (self.root / "error-notify").chmod(0o755)
        # Keep the real filesystem/order/delivery implementation and dispatch.
        # The existing 223-check integration suite covers UI readiness, draft
        # preservation and send(). This unit fixture replaces only send() so
        # repeated UI helper startup cannot exhaust its short queue lifetime.
        source = SUBJECT.read_text()
        dispatch_marker = 'if [ "${1:-}" = --ordered ]; then'
        end_marker = 'if [ "${1:-}" = --box-ready ]; then'
        self.assertEqual(source.count(dispatch_marker), 1)
        self.assertEqual(source.count(end_marker), 1)
        prefix, dispatch = source.split(dispatch_marker)
        dispatch = dispatch_marker + dispatch.split(end_marker)[0]
        functions = self.root / "functions"
        functions.write_text(prefix)
        self.env["PANE_TELL_TEST_FUNCTIONS"] = str(functions)
        (self.root / "dispatch").write_text(dispatch)
        self.subject = self.root / "subject"
        self.subject.write_text('''#!/bin/bash
case "${1:-}" in --deliver|--flush)
  mkdir -p "$FIXTURE/worker-started" "$FIXTURE/worker-complete"
  : > "$FIXTURE/worker-started/$$"
  trap ': > "$FIXTURE/worker-complete/$$"' EXIT ;;
esac
source "$PANE_TELL_TEST_FUNCTIONS"
# Preserve the real predicate and observe its completed decision, not an
# earlier pane-list call. Prefixing its declaration renames the function.
definition=$(declare -f predecessors_done)
eval "real_$definition"
predecessors_done() {
  real_predecessors_done "$@"; local rc=$?
  [ "${ORDER_TAG:-}" != second ] || printf '%s' "$rc" > "$FIXTURE/second-checked"
  [ -z "${ORDER_TAG:-}" ] || printf '%s\\n' "$rc" >> "$FIXTURE/decisions-$ORDER_TAG"
  return "$rc"
}
send() {
  [ ! -f "$FIXTURE/busy" ] || return 1
  if [ "${ORDER_TAG:-}" = first ] && [ -f "$FIXTURE/hold-first" ]; then
    : > "$FIXTURE/first-held"
    while [ -f "$FIXTURE/hold-first" ]; do
      kill -0 "$FIXTURE_PARENT" 2>/dev/null || return 91
      sleep .01
    done
  fi
  [ "${ORDER_TAG:-}" != second ] || : > "$FIXTURE/second-attempt"
  printf '%s\\0%s\\0' "$1" "$3" >> "$FIXTURE/prompts"
}
if [ "${1:-}" = fixture-ordered ]; then shift; ordered "$@"; exit; fi
source "$FIXTURE/dispatch"
exit 92
''')
        panes = [dict(pane_id="w1:p"+p, terminal_id="term_"+p, agent="claude", agent_status="working",
                      agent_session={"value": "s"+p}, label="🟢 👑 receiver" if p != "9" else "sender", tab_id="t")
                 for p in ("M", "N", "9")]
        (self.root / "panes").write_text(json.dumps({"result": {"panes": panes}}))
        for pane in panes:
            (self.root / pane["pane_id"]).write_text(json.dumps({"result": {"pane": dict(pane, scroll={"offset_from_bottom": 0})}}))
        (self.root / "herdr").write_text('''#!/bin/bash
case "$1 $2" in
  "pane list")
    cat "$FIXTURE/panes" ;;
  "pane get") cat "$FIXTURE/$3" ;;
  "pane read")
    text=; [ ! -f "$FIXTURE/busy" ] || text="typed draft"
    printf '────────────────────────────────────────\\n❯ %s\\n────────────────────────────────────────\\n  footer\\n' "$text" ;;
  "agent prompt") printf '%s\\0%s\\0' "$3" "$4" >> "$FIXTURE/prompts" ;;
  "tab list") echo '{"result":{"tabs":[]}}' ;;
  "session list") echo '{"sessions":[]}' ;;
  *) exit 1 ;;
esac
''')
        (self.root / "herdr").chmod(0o755)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        (self.bin / "nohup").write_text('''#!/bin/bash
case " $* " in *" --deliver "*)
  while [ -f "$FIXTURE/hold-worker-start" ]; do
    kill -0 "$FIXTURE_PARENT" 2>/dev/null || exit 91
    sleep .01
  done ;;
esac
exec /usr/bin/nohup "$@"
''')
        (self.bin / "nohup").chmod(0o755)
        self.env["PATH"] = str(self.bin)+":"+self.env["PATH"]
        (self.bin / "mv").write_text('''#!/bin/bash
/bin/mv "$@" || exit
case "$1:$2" in */.joined.*:*/waiting/*)
  [ -f "$FIXTURE/hold-claim" ] || exit 0
  : > "$FIXTURE/claimed"
  while [ ! -e "$FIXTURE/release" ]; do
    kill -0 "$FIXTURE_PARENT" 2>/dev/null || exit 91
    sleep .01
  done
  exit 0;;
esac
''')
        (self.bin / "mv").chmod(0o755)

    def tearDown(self):
        self.capture("before-cleanup")
        (self.root / "release").touch()
        (self.root / "hold-first").unlink(missing_ok=True)
        (self.root / "hold-worker-start").unlink(missing_ok=True)
        # These legacy fixtures have no workers. Remove only their known
        # metadata/payloads so the real new workers can finish during cleanup.
        for payload in self.legacy_payloads:
            payload.unlink(missing_ok=True)
            (self.state / "receivers" / payload.name).unlink(missing_ok=True)
        # A removed payload alone is too early: deliver still has receiver and
        # dependency cleanup to perform. Wait for its real EXIT acknowledgement.
        end = time.monotonic() + 7
        while not self.workers_complete() and time.monotonic() < end:
            (self.root / "busy").unlink(missing_ok=True)
            time.sleep(.02)
        for child in self.children:
            if child.poll() is None:
                child.wait(timeout=9)
        self.capture("after-cleanup")
        if not self.workers_complete():
            # Keep evidence and HOME intact if a worker has not acknowledged
            # completion; deleting them would create a fixture-owned orphan.
            self.tmp._finalizer.detach()
            self.fail("fixture workers did not finish; HOME retained at " + str(self.root))
        self.tmp.cleanup()

    def workers_complete(self):
        started = list((self.root / "worker-started").glob("*"))
        return (not self.waiting() and
                not list((self.state / "receivers").glob("*")) and
                not list((self.state / "order/dependencies").glob("*")) and
                all((self.root / "worker-complete" / path.name).exists() for path in started))

    def capture(self, stage):
        evidence = os.environ.get("PANE_TELL_ORDER_EVIDENCE")
        if evidence:
            target = Path(evidence) / self._testMethodName / stage
            target.mkdir(parents=True, exist_ok=True)
            if self.state.exists():
                shutil.copytree(self.state, target / "state", dirs_exist_ok=True)
            for name in ("prompts", "alerts"):
                if (self.root / name).exists():
                    shutil.copy(self.root / name, target / name)
            for name in ("worker-started", "worker-complete"):
                if (self.root / name).exists():
                    shutil.copytree(self.root / name, target / name, dirs_exist_ok=True)

    def waiting(self):
        return list((self.state / "waiting").glob("*"))

    def wait_for(self, predicate):
        end = time.monotonic() + 7
        while not predicate() and time.monotonic() < end:
            time.sleep(.01)
        self.assertTrue(predicate(), "fixture boundary was not reached")

    def start(self, *args, **extra):
        child = subprocess.Popen(["/bin/bash", str(self.subject), *args], env=dict(self.env, **extra),
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.children.append(child)
        return child

    def run_tell(self, text, pane="w1:pM", **extra):
        child = self.start_ordered("term_"+pane[-1], "relay", pane, "claude", text,
                                   "term_"+pane[-1], "s"+pane[-1], "", "", pane, "👑 receiver", text, **extra)
        out, err = child.communicate(timeout=7)
        self.assertEqual(child.returncode, 0, err)
        return out

    def start_ordered(self, *args, **extra):
        child = subprocess.Popen(["/bin/bash", str(self.subject), "fixture-ordered", *args], env=dict(self.env, **extra),
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.children.append(child)
        return child

    def enqueue(self, text, session="sM", **extra):
        child = self.start_ordered("term_M", "tell", "w1:pM", "claude", text,
                           "term_M", session, "", "", **extra)
        out, err = child.communicate(timeout=7)
        self.assertEqual(child.returncode, 1, err)
        return child

    def legacy(self, name, terminal="term_M", server="default"):
        (self.state / "waiting").mkdir(parents=True, exist_ok=True)
        (self.state / "receivers").mkdir(exist_ok=True)
        payload = self.state / "waiting" / name
        payload.write_text("legacy fixture; no worker is launched")
        (self.state / "receivers" / name).write_text(terminal+"\n"+server+"\n")
        self.legacy_payloads.append(payload)
        return payload

    def prompts(self):
        path = self.root / "prompts"
        parts = path.read_text().split("\0")[:-1] if path.exists() else []
        return list(zip(parts[::2], parts[1::2]))

    def decisions(self, tag):
        path = self.root / ("decisions-" + tag)
        return path.read_text().splitlines() if path.exists() else []

    def test_timer_claim_cannot_be_overtaken_by_urgent_and_other_receiver_is_free(self):
        batch = self.state / "batch/term_M/open"
        batch.mkdir(parents=True)
        (batch / ".t").write_text("1")
        (batch / "00001").write_text("held-message")
        (self.root / "hold-claim").touch()
        timer = self.start("--flush", str(batch.parent), "term_M", PATH=str(self.bin)+":"+self.env["PATH"])
        self.wait_for(lambda: (self.root / "claimed").exists())
        urgent = self.start_ordered("term_M", "relay", "w1:pM", "claude", "prod 허용",
                                    "term_M", "sM", "", "", "w1:pM", "👑 receiver", "prod 허용", PANE_TELL_BATCH="1")
        # Another receiver must complete while this receiver's timer owns its lock.
        self.run_tell("other-receiver", pane="w1:pN")
        try:
            urgent.wait(timeout=.5)
        except subprocess.TimeoutExpired:
            pass
        (self.root / "release").touch()
        timer.communicate(timeout=7)
        urgent.communicate(timeout=7)
        self.wait_for(lambda: len(self.prompts()) == 3)
        ours = [message for pane, message in self.prompts() if pane == "w1:pM"]
        self.assertEqual(ours[0], "held-message")
        self.assertIn("prod 허용", ours[1])
        self.assertFalse((batch.parent / "open").exists())

    def test_busy_receiver_preserves_queue_order_when_first_worker_is_paused(self):
        (self.root / "busy").touch()
        (self.root / "hold-worker-start").touch()
        self.enqueue("first-message", ORDER_TAG="first")
        (self.root / "hold-first").touch()
        self.enqueue("prod 허용", ORDER_TAG="second")
        self.assertEqual(len(self.waiting()), 2)
        (self.root / "busy").unlink()
        # Both lifetimes start only after both durable registrations exist.
        (self.root / "hold-worker-start").unlink()
        self.wait_for(lambda: (self.root / "first-held").exists() and
                      ((self.root / "second-checked").exists() or (self.root / "second-attempt").exists()))
        # The second worker completed its real dependency decision while A's
        # send is paused. A worker that bypasses that predicate sends B now.
        self.assertEqual(self.prompts(), [])
        self.assertEqual((self.root / "second-checked").read_text(), "1")
        (self.root / "hold-first").unlink()
        self.wait_for(lambda: len(self.prompts()) == 2 and not self.waiting())
        self.assertIn("first-message", self.prompts()[0][1])
        self.assertIn("prod 허용", self.prompts()[1][1])
        self.wait_for(lambda: not list((self.state / "receivers").glob("*")))

    def test_legacy_multiple_wait_for_every_payload_and_ignore_other_receivers(self):
        first = self.legacy("000-first")
        last = self.legacy("999-last")
        unrelated = self.legacy("other-terminal", terminal="term_N")
        other_server = self.legacy("other-server", server="/tmp/sessions/workers/herdr.sock")
        self.enqueue("new-message", ORDER_TAG="legacy")
        last.unlink()
        checked = len(self.decisions("legacy"))
        self.wait_for(lambda: len(self.decisions("legacy")) > checked)
        self.assertEqual(self.decisions("legacy")[-1], "1")
        self.assertTrue(first.exists())
        self.assertEqual(self.prompts(), [])
        first.unlink()
        self.wait_for(lambda: len(self.prompts()) == 1)
        self.assertEqual(self.prompts()[0][1], "new-message")
        self.assertTrue(unrelated.exists())
        self.assertTrue(other_server.exists())
        # Fixture-owned legacy payloads have no workers; remove them for teardown.
        unrelated.unlink()
        other_server.unlink()
        self.wait_for(lambda: not list((self.state / "order/dependencies").glob("*")))

    def test_cancelled_middle_does_not_disconnect_the_older_legacy_payload(self):
        oldest = self.legacy("oldest")
        (self.root / "hold-worker-start").touch()
        self.enqueue("old-session-message", session="old-session", ORDER_TAG="first")
        self.enqueue("new-session-message", ORDER_TAG="successor")
        self.assertEqual(len(self.waiting()), 3)
        (self.root / "hold-worker-start").unlink()
        self.wait_for(lambda: not any(p.read_text() == "old-session-message" for p in self.waiting()))
        checked = len(self.decisions("successor"))
        self.wait_for(lambda: len(self.decisions("successor")) > checked)
        self.assertEqual(self.decisions("successor")[-1], "1")
        self.assertTrue(oldest.exists())
        self.assertEqual(self.prompts(), [])
        oldest.unlink()
        self.wait_for(lambda: len(self.prompts()) == 1)
        self.assertEqual(self.prompts()[0][1], "new-session-message")

    def test_one_legacy_payload_blocks_until_it_expires_without_resending_it(self):
        old = self.legacy("single")
        self.enqueue("new-message")
        self.assertEqual(self.prompts(), [])
        undelivered = self.state / "undelivered"
        old.rename(undelivered / old.name)
        self.wait_for(lambda: len(self.prompts()) == 1)
        self.assertEqual(self.prompts(), [("w1:pM", "new-message")])

    def test_receiver_without_legacy_can_send_immediately(self):
        self.assertIn("told", self.run_tell("immediate"))
        self.assertEqual(len(self.prompts()), 1)

    def test_unreadable_or_malformed_active_metadata_blocks_instead_of_overtaking(self):
        for fault in ("unreadable", "malformed", "directory", "non-utf8"):
            with self.subTest(fault=fault):
                old = self.legacy(fault)
                meta = self.state / "receivers" / fault
                try:
                    if fault == "unreadable":
                        meta.chmod(0)
                        with self.assertRaises(PermissionError):
                            meta.read_text()
                    elif fault == "malformed":
                        meta.write_text("missing receiver server\n")
                    elif fault == "directory":
                        meta.parent.chmod(0)
                        with self.assertRaises(PermissionError):
                            list(os.scandir(meta.parent))
                    else:
                        meta.write_bytes(b"\xff\n\xff\n")
                    child = self.start_ordered("term_M", "tell", "w1:pM", "claude", "new-message",
                                               "term_M", "sM", "", "")
                    out, err = child.communicate(timeout=7)
                    self.assertEqual(child.returncode, 2, (out, err))
                    self.assertIn("cannot check the delivery order", err)
                    self.assertIn("message saved at", err)
                    self.assertTrue(old.exists())
                    self.assertEqual(self.prompts(), [])
                    saved = list((self.state / "undelivered").glob("*"))
                    self.assertEqual([path.read_text() for path in saved], ["new-message"])
                    self.assertIn(str(saved[0]), (self.root / "alerts").read_text())
                    self.assertIn("announced:", (self.state / "log").read_text())
                    for path in saved:
                        path.unlink()
                finally:
                    meta.parent.chmod(0o700)
                    meta.chmod(0o600)
                    meta.unlink()
                    old.unlink()

    def test_snapshot_failure_preserves_the_claimed_batch_without_orphan_queue(self):
        old = self.legacy("unreadable")
        meta = self.state / "receivers" / old.name
        meta.chmod(0)
        try:
            batch = self.state / "batch/term_M/open"
            batch.mkdir(parents=True)
            (batch / "00001").write_text("held-message")
            timer = self.start("--flush", str(batch.parent), "term_M")
            out, err = timer.communicate(timeout=7)
            self.assertEqual(timer.returncode, 2, (out, err))
            self.assertIn("message saved at", err)
            self.assertEqual(self.waiting(), [old])
            self.assertEqual([p.read_text() for p in (self.state / "undelivered").glob("*")], ["held-message"])
            self.assertEqual(list((self.state / "order/dependencies").glob("*")), [])
            self.assertEqual(self.prompts(), [])
            self.assertIn("undelivered/", (self.root / "alerts").read_text())
            self.assertIn("announced:", (self.state / "log").read_text())
        finally:
            meta.chmod(0o600)
            old.unlink()

    def test_server_locks_are_independent_and_interrupted_holder_releases_lock(self):
        lock = self.state / "order/default/term_M.lock"
        lock.parent.mkdir(parents=True)
        marker = self.root / "locked"
        holder = subprocess.Popen([sys.executable, "-c", '''
import fcntl, pathlib, sys, time
with open(sys.argv[1], "a") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    pathlib.Path(sys.argv[2]).touch()
    time.sleep(15)
''', str(lock), str(marker)])
        self.children.append(holder)
        try:
            self.wait_for(marker.exists)
            blocked = self.start_ordered("term_M", "tell", "w1:pM", "claude", "default",
                                 "term_M", "sM", "", "")
            other = self.start_ordered("term_M", "tell", "w1:pM", "claude", "workers",
                               "term_M", "sM", "", "", HERDR_SOCKET_PATH="/tmp/sessions/workers/herdr.sock")
            other.communicate(timeout=7)
            self.assertEqual(other.returncode, 0)
            self.assertIsNone(blocked.poll())
            self.assertEqual(self.prompts(), [("w1:pM", "workers")])
        finally:
            holder.terminate()
            holder.wait(timeout=2)
        blocked.communicate(timeout=7)
        self.assertEqual(blocked.returncode, 0)
        self.assertEqual(self.prompts()[-1], ("w1:pM", "default"))


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.argv.remove("--selftest")
    unittest.main()
