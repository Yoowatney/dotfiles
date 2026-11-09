#!/usr/bin/env python3
"""Behavior checks: dropping the queue, client validation or sanitization breaks these."""
import importlib.util
from concurrent.futures import ThreadPoolExecutor
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time
import unittest
import uuid

SCRIPT = Path(__file__).with_name("herdr-error-notify.py")


class ErrorNotifyTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), "the durable error gateway is missing")
        spec = importlib.util.spec_from_file_location("error_notify", SCRIPT)
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = self.mod.Store(Path(self.tmp.name))

    def terminal(self, pid=100, default=True):
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        tty = os.ttyname(slave).removeprefix("/dev/")
        command = "herdr" if default else "herdr session attach workers"
        candidate = self.mod.Client(pid, os.getuid(), tty, "Mon Nov 9 12:00:00 2026", "herdr", command)
        return master, candidate

    def read(self, master):
        if not select.select([master], [], [], 0.05)[0]:
            return b""
        return os.read(master, 8192)

    def test_real_pty_receives_exactly_one_sanitized_error(self):
        master, client = self.terminal()
        event = self.store.enqueue("Failure\x1b]9;bad\x07", "A\nB\x9cC")
        self.assertEqual(self.store.drain(lambda: [client]), 1)
        payload = self.read(master)
        self.assertEqual(payload, b"\x1b]9;Failure ]9;bad: A B C\x1b\\")
        self.assertEqual(self.store.export(), [])
        self.assertEqual(self.store.drain(lambda: [client]), 0)
        self.assertEqual(self.read(master), b"")
        self.assertTrue(uuid.UUID(event["id"]))

    def test_no_client_stays_pending_then_retries(self):
        master, client = self.terminal()
        event = self.store.enqueue("Failure", "No owner")
        self.assertEqual(self.store.drain(lambda: []), 0)
        self.assertEqual(self.store.export(), [event])
        self.assertEqual(self.store.drain(lambda: [client]), 1)
        self.assertIn(b"No owner", self.read(master))

    def test_default_client_preferred_and_only_one_terminal_written(self):
        attached_master, attached = self.terminal(pid=10, default=False)
        default_master, default = self.terminal(pid=20)
        self.store.enqueue("Failure", "One terminal")
        self.assertEqual(self.store.drain(lambda: [attached, default]), 1)
        self.assertEqual(self.read(attached_master), b"")
        self.assertIn(b"One terminal", self.read(default_master))

    def test_reused_pid_or_tty_does_not_receive_error(self):
        master, client = self.terminal()
        changed = client._replace(started="Mon Nov 9 12:01:00 2026")
        snapshots = iter([[client], [changed]])
        event = self.store.enqueue("Failure", "Do not lose")
        self.assertEqual(self.store.drain(lambda: next(snapshots)), 0)
        self.assertEqual(self.read(master), b"")
        self.assertEqual(self.store.export(), [event])

    def test_open_error_keeps_pending(self):
        _, client = self.terminal()
        client = client._replace(tty="ttys999999999")
        event = self.store.enqueue("Failure", "Retry later")
        self.assertEqual(self.store.drain(lambda: [client]), 0)
        self.assertEqual(self.store.export(), [event])

    def test_inventory_rejects_server_bridge_no_tty_and_other_uid(self):
        prefix = f" 123 {os.getuid()} ttys001 Mon Nov 9 12:00:00 2026 herdr "
        data = "\n".join(prefix + args for args in [
            "herdr", "herdr session attach workers", "herdr server",
            "herdr remote-client-bridge", "herdr notification show error",
        ])
        data += f"\n124 {os.getuid()} ?? Mon Nov 9 12:00:00 2026 herdr herdr"
        data += "\n125 999999 ttys002 Mon Nov 9 12:00:00 2026 herdr herdr"
        clients = self.mod.parse_clients(data)
        self.assertEqual([c.command for c in clients], ["herdr", "herdr session attach workers"])

    @unittest.skipUnless(sys.platform == "darwin" and shutil.which("herdr"),
                         "requires macOS ps and the installed herdr client")
    def test_real_macos_ps_recognizes_absolute_path_client_on_pty(self):
        executable = str(Path(shutil.which("herdr")).absolute())
        pid, master = pty.fork()
        if pid == 0:
            # A nested client exits immediately; this PTY represents a separate
            # local terminal, not the session running the test.
            for key in list(os.environ):
                if key.startswith("HERDR") or key == "TMUX":
                    os.environ.pop(key)
            os.environ["TERM"] = "xterm-256color"
            fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 100, 0, 0))
            os.execv(executable, [executable])
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                result = subprocess.run(["/bin/ps", "-p", str(pid), "-o", "ucomm="],
                                        capture_output=True, text=True, check=False)
                if result.stdout.strip() == "herdr":
                    break
                time.sleep(0.05)
            clients = self.mod.client_inventory()
            matches = [client for client in clients if client.pid == pid]
            self.assertEqual(len(matches), 1, "absolute-path herdr on a real PTY must be eligible")
            self.assertTrue(matches[0].command.startswith(executable))
            self.assertRegex(matches[0].tty, r"^ttys[0-9]+$")
        finally:
            os.close(master)
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            os.waitpid(pid, 0)

    def test_export_import_ack_preserve_ids_and_deduplicate(self):
        event = self.store.enqueue("Failure", "Body")
        other = self.mod.Store(Path(self.tmp.name) / "other")
        self.assertEqual(other.import_events([event]), 1)
        self.assertEqual(other.import_events([event]), 0)
        self.assertEqual(other.export(), [event])
        other.ack(event["id"])
        self.assertEqual(other.export(), [])
        self.assertEqual(other.import_events([event]), 0)
        self.store.ack(event["id"])
        self.assertEqual(self.store.export(), [])

    def test_duplicate_import_after_successful_drain_never_replays(self):
        master, client = self.terminal()
        event = self.store.enqueue("Failure", "Exactly once after durable completion")
        self.assertEqual(self.store.drain(lambda: [client]), 1)
        self.assertIn(b"Exactly once", self.read(master))
        reopened = self.mod.Store(Path(self.tmp.name))
        self.assertEqual(reopened.import_events([event]), 0)
        self.assertEqual(reopened.drain(lambda: [client]), 0)
        self.assertEqual(self.read(master), b"")

    def test_corrupt_queue_is_preserved_and_never_reset(self):
        self.store.enqueue("Failure", "Must remain recoverable")
        damaged = self.store.path.read_bytes() + b"damaged"
        self.store.path.write_bytes(damaged)
        with self.assertRaises(ValueError):
            self.store.enqueue("Next failure", "Do not overwrite existing queue")
        self.assertEqual(self.store.path.read_bytes(), damaged)

    def test_concurrent_process_imports_do_not_drop_events(self):
        env = dict(os.environ, XDG_STATE_HOME=self.tmp.name)
        events = [dict(id=str(uuid.uuid4()), title="Failure", body=f"Event {i}",
                       created_at="2026-11-09T12:00:00+00:00") for i in range(8)]

        def importer(event):
            return subprocess.run([sys.executable, str(SCRIPT), "--import"],
                                  input=json.dumps([event]), text=True,
                                  capture_output=True, env=env)

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(importer, events))
        self.assertEqual([result.returncode for result in results], [0] * 8,
                         [result.stderr for result in results])
        store = self.mod.Store(Path(self.tmp.name) / "herdr-error-notify")
        self.assertEqual({item["id"] for item in store.export()}, {item["id"] for item in events})

    def test_invalid_import_is_atomic_and_ack_rejects_paths(self):
        event = self.store.enqueue("Failure", "Body")
        with self.assertRaises(ValueError):
            self.store.import_events([event, dict(event, id="../../bad")])
        self.assertEqual(self.store.export(), [event])
        with self.assertRaises(ValueError):
            self.store.ack("../../bad")
        with self.assertRaises(ValueError):
            self.store.import_events([dict(event, body="changed")])

    def test_invalid_unicode_cannot_poison_the_pending_queue(self):
        event = self.store.enqueue("Failure", "Existing valid error")
        malformed = dict(event, id=str(uuid.uuid4()), body="bad\ud800")
        with self.assertRaises(ValueError):
            self.mod.validate_event(malformed)
        with self.assertRaises(ValueError):
            self.store.import_events([malformed])
        self.assertEqual(self.store.export(), [event])

    def test_cli_export_import_ack_and_no_tty_override(self):
        env = dict(os.environ, XDG_STATE_HOME=self.tmp.name)
        event = self.store.enqueue("Failure", "Body")
        result = subprocess.run([sys.executable, str(SCRIPT), "--import"],
                                input=json.dumps([event]), text=True, capture_output=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run([sys.executable, str(SCRIPT), "--export"],
                                text=True, capture_output=True, env=env)
        self.assertEqual(json.loads(result.stdout), [event])
        result = subprocess.run([sys.executable, str(SCRIPT), "--ack", event["id"]], capture_output=True, env=env)
        self.assertEqual(result.returncode, 0)
        result = subprocess.run([sys.executable, str(SCRIPT), "--tty", "/dev/ttys001"],
                                capture_output=True, env=env)
        self.assertNotEqual(result.returncode, 0)

    def test_cli_ack_many_ids_in_one_call(self):
        env = dict(os.environ, XDG_STATE_HOME=self.tmp.name)
        events = [dict(id=str(uuid.uuid4()), title="Failure", body=f"Event {i}",
                       created_at="2026-11-09T12:00:00+00:00") for i in range(2)]
        imported = subprocess.run([sys.executable, str(SCRIPT), "--import"],
                                  input=json.dumps(events), text=True, capture_output=True, env=env)
        self.assertEqual(imported.returncode, 0, imported.stderr)
        result = subprocess.run([sys.executable, str(SCRIPT), "--ack", *[e["id"] for e in events]],
                                text=True, capture_output=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(imported.stdout), {"imported": 2})
        self.assertEqual(json.loads(result.stdout), {"acknowledged": 2})
        store = self.mod.Store(Path(self.tmp.name) / "herdr-error-notify")
        self.assertEqual(store.export(), [])


if __name__ == "__main__":
    unittest.main()
