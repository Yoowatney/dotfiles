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
        # Observe kicks without contacting a real pane, and join the background
        # function before assertions so a missing kick is deterministic.
        self.kicks = self.root / "kicks"
        bash_env = self.root / "bash-env"
        bash_env.write_text('nohup() { printf "kick\\n" >> "$HANDOFF_TEST_KICKS"; }\ntrap wait EXIT\n')
        self.env.update(BASH_ENV=str(bash_env), HANDOFF_TEST_KICKS=str(self.kicks))
        self.file = self.root / "agent-handoff/test-pane.md"
        self.meta = dict(task="example task", completed="step one", remaining="step two",
                         verification="not verified", resume="read evidence", code="none", repositories=[])

    def runho(self, command, text=""):
        return subprocess.run(["bash", str(HERE / "handoff"), command], input=text,
                              text=True, env=self.env, capture_output=True)

    def memo(self):
        return "# Continue example\n\n```handoff\n" + json.dumps(self.meta) + "\n```\n"

    def transcript(self, sid, source="cli", originator="codex-tui"):
        path = self.root / (sid + ".jsonl")
        path.write_text(json.dumps(dict(type="session_meta", payload=dict(
            id=sid, source=source, originator=originator))) + "\n")
        return path

    def start(self, sid, transcript=None):
        payload = dict(source="startup", session_id=sid)
        if transcript is not None:
            payload["transcript_path"] = str(transcript)
        return subprocess.run(["bash", str(HERE / "handoff"), "session", "codex"],
            input=json.dumps(payload), env=self.env, text=True, capture_output=True)

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

    def test_loaded_metadata_must_still_have_complete_required_fields(self):
        # A damaged saved memo must not bypass the validation used by save.
        for key, invalid in [(key, " \n ") for key in
                             ("task", "completed", "remaining", "verification", "resume")] + [
                                 ("code", "invalid"), ("version", True)]:
            with self.subTest(field=key, value=invalid):
                self.assertEqual(self.runho("save", self.memo()).returncode, 0)
                data = dict(self.meta, version=1)
                data[key] = invalid
                self.file.write_text("```handoff\n" + json.dumps(data) + "\n```\n")
                ctx = self.load()
                self.assertIn("필수 항목 미확인", ctx)
                self.assertNotIn("해당 없음", ctx)

    def test_explicitly_unverified_evidence_never_confirms_code_identity(self):
        repo = self.repo("repo")
        for evidence in ("not verified", " Not Verified. ", "NOT VERIFIED"):
            with self.subTest(evidence=evidence):
                self.meta.update(code="repositories", repositories=[dict(path=str(repo),
                    verified_commit=self.git(repo, "rev-parse", "HEAD"),
                    verified_dirty=False, evidence=evidence)])
                self.assertEqual(self.runho("save", self.memo()).returncode, 0)
                ctx = self.load()
                self.assertIn("이전 검증을 그대로 사용할 수 없음", ctx)
                self.assertNotIn("코드 상태 일치", ctx)

    def test_loaded_repository_evidence_must_remain_valid(self):
        repo = self.repo("repo")
        self.meta.update(code="repositories", repositories=[dict(path=str(repo),
            verified_commit=self.git(repo, "rev-parse", "HEAD"), verified_dirty=False,
            evidence="test run")])
        for field, invalid in [("evidence", "   "), ("verified_dirty", 0)]:
            with self.subTest(field=field):
                self.assertEqual(self.runho("save", self.memo()).returncode, 0)
                data = json.loads(self.file.read_text().split("```handoff\n")[1].split("\n```")[0])
                data["repositories"][0][field] = invalid
                self.file.write_text("```handoff\n" + json.dumps(data) + "\n```\n")
                ctx = self.load()
                self.assertIn("필수 항목 미확인", ctx)
                self.assertNotIn("코드 상태 일치", ctx)

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
        data = json.loads(saved.split("```handoff\n")[1].split("\n```")[0])
        self.assertEqual([entry["current"] for entry in data["repositories"]], [
            dict(path=str(repo.resolve()), branch=self.git(repo, "branch", "--show-current"),
                 commit=self.git(repo, "rev-parse", "HEAD"), dirty=False) for repo in (a, b)])
        self.assertIsNone(data["repositories"][1]["verified_commit"])
        self.assertIsNone(data["repositories"][1]["verified_dirty"])
        ctx = self.load()
        self.assertIn("이전 검증을 그대로 사용할 수 없음", ctx)
        self.assertIn(str(b), ctx)

    def test_changed_commit_dirty_branch_and_missing_repository(self):
        for change in ("dirty", "commit", "branch", "missing"):
            # Each change starts with a clean, verified snapshot. A previous dirty
            # or mismatched commit must not make the branch/missing tests pass.
            repo = self.repo(change)
            self.meta.update(code="repositories", repositories=[dict(path=str(repo),
                verified_commit=self.git(repo, "rev-parse", "HEAD"), verified_dirty=False, evidence="test run")])
            original = self.memo()
            self.assertEqual(self.runho("save", original).returncode, 0)
            self.assertIn("코드 상태 일치", self.load())
            self.assertEqual(self.runho("save", original).returncode, 0)
            if change == "dirty":
                (repo / "untracked").write_text("new")
            elif change == "commit":
                (repo / "file").write_text("changed")
                self.git(repo, "add", ".")
                self.git(repo, "commit", "-qm", "Change")
            elif change == "branch":
                self.git(repo, "checkout", "-qb", "other")
            else:
                repo.rename(self.root / "moved")
            self.assertIn("이전 검증을 그대로 사용할 수 없음", self.load(), change)

    def test_worktrees_keep_distinct_branch_and_dirty_snapshots(self):
        repo = self.repo("repo")
        worktree = self.root / "worktree"
        self.git(repo, "worktree", "add", "-qb", "topic", str(worktree))
        (repo / "file").write_text("uncommitted")
        self.meta.update(code="repositories", repositories=[dict(path=str(path),
            verified_commit=self.git(path, "rev-parse", "HEAD"), verified_dirty=False,
            evidence="test run") for path in (repo, worktree)])
        self.assertEqual(self.runho("save", self.memo()).returncode, 0)
        saved = json.loads(self.file.read_text().split("```handoff\n")[1].split("\n```")[0])
        a, b = [entry["current"] for entry in saved["repositories"]]
        self.assertTrue(a["dirty"])
        self.assertFalse(b["dirty"])
        self.assertNotEqual(a["branch"], b["branch"])
        ctx = self.load()
        self.assertIn(str(repo.resolve()) + ": 이전 검증을 그대로 사용할 수 없음", ctx)
        self.assertIn(str(worktree.resolve()) + ": 코드 상태 일치", ctx)

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
        old = self.transcript("one")
        with old.open("a") as stream:
            stream.write("\n".join(json.dumps(dict(type="response_item", payload=dict(type="message", role=role,
            content=[dict(type=kind, text=text)]))) for role, kind, text in
            [("user", "input_text", "example request"), ("assistant", "output_text", "example answer")]) + "\n")
        self.assertEqual(self.start("one", old).stdout, "")
        self.assertEqual(self.runho("save", self.memo()).returncode, 0)
        new = self.transcript("two")
        saved = self.start("two", new)
        self.assertIn("Continue example", saved.stdout)
        self.assertEqual(self.start("two", new).stdout, "")
        self.assertEqual(self.kicks.read_text().splitlines(), ["kick"])
        # A later unsaved restart reads Codex messages, never Claude's .transcript pointer.
        fallback = self.start("three", self.transcript("three"))
        # The previous session was empty. Resume the original transcript first.
        self.assertEqual(fallback.stdout, "")
        self.start("one", old)
        fallback = self.start("four", self.transcript("four"))
        self.assertIn("example request", fallback.stdout)
        self.assertIn("example answer", fallback.stdout)
        self.assertIn("필수 항목 미확인", fallback.stdout)
        self.assertEqual(len(self.kicks.read_text().splitlines()), 2)

    def test_noninteractive_codex_preserves_memo_record_and_sends_no_kick(self):
        for source, originator in [("exec", "codex_exec"), ("exec", "codex-tui"),
                ({"subagent": {"thread_spawn": {}}}, "codex-tui"), ("vscode", "codex-vscode")]:
            for inherited in (None, "child"):
                with self.subTest(source=source, inherited=inherited):
                    if inherited is None:
                        self.env.pop("CODEX_THREAD_ID", None)
                    else:
                        self.env["CODEX_THREAD_ID"] = inherited
                    self.assertEqual(self.runho("save", self.memo()).returncode, 0)
                    record = Path(str(self.file) + ".codex-session")
                    record.write_text('{"session_id":"parent","transcript_path":"parent.jsonl"}')
                    before = {p: p.read_bytes() for p in (self.file, record)}
                    r = self.start("child", self.transcript("child", source, originator))
                    self.assertEqual(r.returncode, 0, r.stderr)
                    self.assertEqual(r.stdout, "")
                    self.assertEqual({p: p.read_bytes() for p in before}, before)
                    self.assertFalse(self.kicks.exists())

    def test_unverified_codex_identity_does_not_touch_state(self):
        for header in ("", "[]\n", "broken\n", json.dumps(dict(type="session_meta", payload=None)),
                json.dumps(dict(type="session_meta", payload=dict(id="other", source="cli", originator="codex-tui"))),
                json.dumps(dict(type="session_meta", payload=dict(id="child")))):
            with self.subTest(header=header):
                self.assertEqual(self.runho("save", self.memo()).returncode, 0)
                path = self.root / "unverified.jsonl"
                path.write_text(header)
                r = self.start("child", path)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(r.stdout, "")
                self.assertTrue(self.file.exists())
                self.assertFalse(Path(str(self.file) + ".codex-session").exists())
                self.assertFalse(self.kicks.exists())

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
        old = self.transcript("old")
        with old.open("a") as stream:
            stream.write('[]\n{"type":"response_item","payload":null}\n' +
                         '{"type":"response_item","payload":{"type":"message","content":null}}\n')
        self.file.parent.mkdir()
        Path(str(self.file) + ".codex-session").write_text(json.dumps(dict(session_id="old", transcript_path=str(old))))
        r = subprocess.run(["bash", str(HERE / "handoff"), "session", "codex"],
            input=json.dumps(dict(session_id="new", transcript_path=str(self.transcript("new")))),
            env=self.env, text=True, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")

    def test_interactive_restart_does_not_replay_old_exec_record(self):
        old = self.transcript("old-exec", "exec", "codex_exec")
        with old.open("a") as stream:
            stream.write(json.dumps(dict(type="response_item", payload=dict(type="message",
                role="user", content=[dict(type="input_text", text="unrelated child request")]))) + "\n")
        self.file.parent.mkdir()
        record = Path(str(self.file) + ".codex-session")
        record.write_text(json.dumps(dict(session_id="old-exec", transcript_path=str(old))))
        r = self.start("new", self.transcript("new"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertEqual(json.loads(record.read_text())["session_id"], "new")
        self.assertFalse(self.kicks.exists())

    def test_named_server_interactive_restart_loads_only_its_own_handoff(self):
        self.assertEqual(self.runho("save", self.memo()).returncode, 0)
        self.env["HERDR_SOCKET_PATH"] = "/tmp/herdr/sessions/workers/herdr.sock"
        self.assertEqual(self.runho("save", self.memo() + "workers only").returncode, 0)
        r = self.start("worker", self.transcript("worker"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("workers only", r.stdout)
        self.assertTrue(self.file.exists())
        self.assertTrue((self.file.parent / "workers/test-pane.md.codex-session").exists())
        self.assertEqual(self.kicks.read_text().splitlines(), ["kick"])

    def test_codex_without_transcript_path_loads_saved_note_and_resolves_session_meta(self):
        self.assertEqual(self.runho("save", self.memo()).returncode, 0)
        self.env["CODEX_HOME"] = str(self.root / "codex")
        log = self.root / "codex/sessions/2026/11/11/rollout-test-session-one.jsonl"
        log.parent.mkdir(parents=True)
        log.write_text(json.dumps(dict(type="session_meta", payload=dict(
            id="session-one", source="cli", originator="codex-tui"))) + "\n")
        r = subprocess.run(["bash", str(HERE / "handoff"), "session", "codex"],
            input=json.dumps(dict(session_id="session-one")), env=self.env, text=True, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Continue example", r.stdout)
        record = json.loads(Path(str(self.file) + ".codex-session").read_text())
        self.assertEqual(record["transcript_path"], str(log))
        # Without a rollout, the hook cannot distinguish a pane from codex exec.
        self.assertEqual(self.runho("save", self.memo()).returncode, 0)
        r = subprocess.run(["bash", str(HERE / "handoff"), "session", "codex"],
            input=json.dumps(dict(session_id="session-two")), env=self.env, text=True, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertTrue(self.file.exists())
        self.assertEqual(json.loads(Path(str(self.file) + ".codex-session").read_text()), record)


if __name__ == "__main__":
    unittest.main()
