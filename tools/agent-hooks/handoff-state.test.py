#!/usr/bin/env python3
"""Behavior tests: validation, independent Git evidence and failure preservation."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent


class HandoffTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = dict(os.environ, XDG_STATE_HOME=str(self.root), HERDR_PANE_ID="test-pane")
        self.env.pop("HERDR_SOCKET_PATH", None)
        self.env.pop("CODEX_THREAD_ID", None)
        self.file = self.root / "agent-handoff/test-pane.md"
        self.meta = dict(task="example task", completed="step one", remaining="step two",
                         verification="not verified", resume="read evidence", code="none", repositories=[])

    def runho(self, command, text=""):
        return subprocess.run(["bash", str(HERE / "handoff"), command], input=text,
                              text=True, env=self.env, capture_output=True)

    def memo(self):
        return "# Continue example\n\n```handoff\n" + json.dumps(self.meta) + "\n```\n"

    def git(self, repo, *args):
        return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()

    def repo(self, name):
        repo = self.root / name
        repo.mkdir()
        self.git(repo, "init", "-q")
        self.git(repo, "config", "user.email", "test@example.invalid")
        self.git(repo, "config", "user.name", "Test")
        (repo / "file").write_text("one")
        self.git(repo, "add", ".")
        self.git(repo, "commit", "-qm", "Initial")
        return repo

    def load(self):
        r = subprocess.run(["python3", str(HERE / "handoff-state.py"), "load"],
                           env=self.env, text=True, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]

    def test_missing_fields_preserve_prior_memo(self):
        self.file.parent.mkdir()
        self.file.write_text("prior")
        for key in list(self.meta):
            with self.subTest(key=key):
                old = self.meta.pop(key)
                r = self.runho("save", self.memo())
                self.meta[key] = old
                self.assertNotEqual(r.returncode, 0, "incomplete handoff accepted: " + key)
                self.assertEqual(self.file.read_text(), "prior")

    def test_no_code_and_legacy(self):
        saved = self.runho("save", self.memo())
        self.assertEqual(saved.returncode, 0)
        self.assertIn("/new", saved.stdout)
        self.assertIn("first regular message", saved.stdout)
        self.assertIn("해당 없음", self.load())
        self.file.write_text("legacy note")
        self.assertIn("필수 항목 미확인", self.load())

    def test_each_repo_records_current_and_verified_separately(self):
        a, b = self.repo("a"), self.repo("b")
        self.meta.update(code="repositories", repositories=[
            dict(path=str(a), verified_commit=self.git(a, "rev-parse", "HEAD"), verified_dirty=False, evidence="test run"),
            dict(path=str(b), verified_commit=None, verified_dirty=None, evidence="not verified")])
        self.assertEqual(self.runho("save", self.memo()).returncode, 0)
        saved = self.file.read_text()
        self.assertIn(str(a), saved)
        self.assertIn(str(b), saved)
        self.assertIn('"current"', saved)
        ctx = self.load()
        self.assertIn("이전 검증을 그대로 사용할 수 없음", ctx)
        self.assertIn(str(b), ctx)

    def test_changed_commit_dirty_branch_and_missing_repository(self):
        repo = self.repo("repo")
        self.meta.update(code="repositories", repositories=[dict(path=str(repo),
            verified_commit=self.git(repo, "rev-parse", "HEAD"), verified_dirty=False, evidence="test run")])
        original = self.memo()
        self.assertEqual(self.runho("save", original).returncode, 0)
        self.assertIn("코드 상태 일치", self.load())
        for change in ("dirty", "commit", "branch", "missing"):
            self.assertEqual(self.runho("save", original).returncode, 0)
            if change == "dirty":
                (repo / "untracked").write_text("new")
            elif change == "commit":
                self.git(repo, "add", ".")
                self.git(repo, "commit", "-qm", "Change")
            elif change == "branch":
                self.git(repo, "checkout", "-qb", "other")
            else:
                repo.rename(self.root / "moved")
            self.assertIn("이전 검증을 그대로 사용할 수 없음", self.load(), change)

    def test_malformed_metadata_and_unknown_verification(self):
        self.assertNotEqual(self.runho("save", "```handoff\n{broken\n```\n").returncode, 0)
        repo = self.repo("repo")
        self.meta.update(code="repositories", repositories=[dict(path=str(repo),
            verified_commit=self.git(repo, "rev-parse", "HEAD"), verified_dirty=None, evidence="unknown")])
        self.assertEqual(self.runho("save", self.memo()).returncode, 0)
        self.assertIn("이전 검증을 그대로 사용할 수 없음", self.load())

    def test_replace_failure_keeps_previous_and_cleans_temporary_file(self):
        spec = importlib.util.spec_from_file_location("handoff_state", HERE / "handoff-state.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.file.parent.mkdir()
        self.file.write_text("prior")
        with patch.object(module.os, "replace", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                module.atomic_write(self.file, "new")
        self.assertEqual(self.file.read_text(), "prior")
        self.assertEqual(list(self.file.parent.iterdir()), [self.file])

    def test_symlink_entrypoint_uses_real_adjacent_helper(self):
        link = self.root / "handoff"
        link.symlink_to(HERE / "handoff")
        r = subprocess.run([str(link), "save"], input=self.memo(), text=True, env=self.env, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_codex_session_loads_once_and_falls_back_to_its_own_transcript(self):
        def start(sid, transcript):
            return subprocess.run(["bash", str(HERE / "handoff"), "session", "codex"],
                input=json.dumps(dict(source="startup", session_id=sid, transcript_path=str(transcript))),
                env=self.env, text=True, capture_output=True)
        old = self.root / "old.jsonl"
        old.write_text("\n".join(json.dumps(dict(type="response_item", payload=dict(type="message", role=role,
            content=[dict(type=kind, text=text)]))) for role, kind, text in
            [("user", "input_text", "example request"), ("assistant", "output_text", "example answer")]) + "\n")
        self.assertEqual(start("one", old).stdout, "")
        self.assertEqual(self.runho("save", self.memo()).returncode, 0)
        saved = start("two", self.root / "new.jsonl")
        self.assertIn("Continue example", saved.stdout)
        self.assertEqual(start("two", self.root / "new.jsonl").stdout, "")
        # A later unsaved restart reads Codex messages, never Claude's .transcript pointer.
        start("three", old)
        fallback = start("four", self.root / "last.jsonl")
        self.assertIn("example request", fallback.stdout)
        self.assertIn("example answer", fallback.stdout)
        self.assertIn("필수 항목 미확인", fallback.stdout)

    def test_long_memo_keeps_warning_inline(self):
        self.assertEqual(self.runho("save", self.memo() + "x" * 3000).returncode, 0)
        self.file.write_text("old note" + "x" * 3000)
        ctx = self.load()
        self.assertIn("필수 항목 미확인", ctx)
        self.assertIn(".loaded", ctx)

    def test_nested_codex_does_not_consume_parent_memo(self):
        self.assertEqual(self.runho("save", self.memo()).returncode, 0)
        self.env["CODEX_THREAD_ID"] = "parent"
        r = subprocess.run(["bash", str(HERE / "handoff"), "session", "codex"],
            input=json.dumps(dict(session_id="child", transcript_path="/unknown")),
            env=self.env, text=True, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertTrue(self.file.exists())

    def test_malformed_codex_transcript_rows_are_skipped(self):
        old = self.root / "old.jsonl"
        old.write_text('[]\n{"type":"response_item","payload":null}\n' +
                       '{"type":"response_item","payload":{"type":"message","content":null}}\n')
        self.file.parent.mkdir()
        Path(str(self.file) + ".codex-session").write_text(json.dumps(dict(session_id="old", transcript_path=str(old))))
        r = subprocess.run(["bash", str(HERE / "handoff"), "session", "codex"],
            input=json.dumps(dict(session_id="new", transcript_path=str(self.root / "new"))),
            env=self.env, text=True, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")

    def test_codex_without_transcript_path_loads_saved_note_and_resolves_session_meta(self):
        self.assertEqual(self.runho("save", self.memo()).returncode, 0)
        self.env["CODEX_HOME"] = str(self.root / "codex")
        log = self.root / "codex/sessions/2026/11/11/rollout-test-session-one.jsonl"
        log.parent.mkdir(parents=True)
        log.write_text(json.dumps(dict(type="session_meta", payload=dict(id="session-one"))) + "\n")
        r = subprocess.run(["bash", str(HERE / "handoff"), "session", "codex"],
            input=json.dumps(dict(session_id="session-one")), env=self.env, text=True, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Continue example", r.stdout)
        record = json.loads(Path(str(self.file) + ".codex-session").read_text())
        self.assertEqual(record["transcript_path"], str(log))
        # A missing rollout must not prevent an explicit memo from loading either.
        self.assertEqual(self.runho("save", self.memo()).returncode, 0)
        r = subprocess.run(["bash", str(HERE / "handoff"), "session", "codex"],
            input=json.dumps(dict(session_id="session-two")), env=self.env, text=True, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Continue example", r.stdout)


if __name__ == "__main__":
    unittest.main()
