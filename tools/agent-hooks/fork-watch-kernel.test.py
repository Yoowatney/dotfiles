#!/usr/bin/env python3
"""Regression checks for the live kernel-table selftest (--selftest)."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("fork_watch", Path(__file__).with_name("fork-watch.py"))
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)


class KernelSelftest(unittest.TestCase):
    def run_selftest(self):
        with patch.object(sys, "argv", [watch.__file__, "--selftest"]), contextlib.redirect_stdout(io.StringIO()):
            watch.selftest()

    def test_unrelated_process_can_change_group_between_readers(self):
        # This child is ours, but not one of the selftest's stable fixtures.
        # Synchronize a real setpgid exactly between the kernel read and ps.
        child = subprocess.Popen(
            [sys.executable, "-I", "-c",
             'import os,sys; print("ready",flush=True); sys.stdin.readline(); '
             'os.setpgid(0,0); print("changed",flush=True); sys.stdin.readline()'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        )
        run = subprocess.run
        changed = False

        def run_after_change(args, *pos, **kwargs):
            nonlocal changed
            if args[0] == "/bin/ps" and not changed:
                before = next(p for p in watch.list_procs() if p.pid == child.pid)
                child.stdin.write("go\n")
                child.stdin.flush()
                self.assertEqual(child.stdout.readline().strip(), "changed")
                self.assertNotEqual(before.pgid, os.getpgid(child.pid))
                changed = True
            return run(args, *pos, **kwargs)

        try:
            self.assertEqual(child.stdout.readline().strip(), "ready")
            with patch.object(watch.subprocess, "run", run_after_change):
                self.run_selftest()
            self.assertTrue(changed, "the test must cross the real kernel/ps boundary")
        finally:
            child.stdin.close()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
            child.stdout.close()

    def test_incorrect_kernel_group_is_still_rejected(self):
        # A wrong parser offset must still fail; ignoring mismatches is not a fix.
        list_procs = watch.list_procs

        def wrong_groups():
            return [p._replace(pgid=p.pgid + 1) for p in list_procs()]

        with patch.object(watch, "list_procs", wrong_groups), self.assertRaises(AssertionError):
            self.run_selftest()


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
