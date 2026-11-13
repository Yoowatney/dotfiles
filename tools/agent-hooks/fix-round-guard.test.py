#!/usr/bin/env python3
"""Regression coverage for the narrowly scoped third-fix declaration."""
import importlib.util
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest


spec = importlib.util.spec_from_file_location("fix_round_guard", Path(__file__).with_name("fix-round-guard.py"))
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class LocalFixContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.target = self.root / "known fix.py"
        self.target.write_text("# concrete change\n")
        self.declaration = "FIX_ROUND_SCOPE=local-pr FIX_ROUND_TARGETS=" + shlex.quote(str(self.target)) + " "
        self.spawn = 'pane-spawn "third fix" "03:34:57 entry; fix it"'
        self.tell = 'pane-tell workers/w1:p1 "03:34:57 entry; fix it"'

    def test_contract_on_actual_spawn_and_tell_is_accepted(self):
        for action in (self.spawn, self.tell):
            with self.subTest(action=action):
                self.assertTrue(guard.concrete_local_fix(self.declaration + action))

    def test_contract_on_other_simple_command_does_not_authorize_action(self):
        for action in (self.spawn, self.tell):
            for separator in ("; ", " && ", " || ", " | ", "\n"):
                with self.subTest(action=action, separator=separator):
                    self.assertFalse(guard.concrete_local_fix(self.declaration + "true" + separator + action))

    def test_unrelated_command_before_action_contract_is_allowed(self):
        for action in (self.spawn, self.tell):
            self.assertTrue(guard.concrete_local_fix("true; " + self.declaration + action))

    def test_declaration_on_separate_line_does_not_authorize_action(self):
        self.assertFalse(guard.concrete_local_fix(self.declaration.rstrip() + "\n" + self.spawn))

    def test_quoted_declaration_for_display_does_not_authorize_action(self):
        target = self.root / "display.py"
        target.write_text("# concrete change\n")
        display = f"echo \"note; FIX_ROUND_SCOPE=local-pr FIX_ROUND_TARGETS={target} pane-spawn 'fake' '03:34:57 entry'\""
        self.assertFalse(guard.concrete_local_fix(display + "; " + self.spawn))

    def test_commented_contract_does_not_authorize_action(self):
        self.assertFalse(guard.concrete_local_fix("# ; " + self.declaration + self.spawn + "\n" + self.spawn))

    def test_other_pane_command_contract_does_not_authorize_action(self):
        self.assertFalse(guard.concrete_local_fix(
            self.declaration + 'pane-spawn "unrelated" "no citation"; ' + self.spawn))

    def test_quoted_pane_text_before_real_contract_is_allowed(self):
        self.assertTrue(guard.concrete_local_fix(
            'echo "display; pane-spawn \'fake\' \'no citation\'"; ' + self.declaration + self.spawn))

    def test_executable_substitutions_are_real_pane_actions(self):
        for command in ('echo "$(' + self.spawn + ')"', 'out=$(' + self.spawn + ')',
                        'echo `' + self.spawn + '`', 'echo "$(echo $(' + self.spawn + '))"'):
            with self.subTest(command=command):
                self.assertEqual(len(guard.pane_commands(command)), 1)
                self.assertFalse(guard.concrete_local_fix(command))

    def test_single_quoted_substitution_is_only_display_text(self):
        self.assertEqual(guard.pane_commands("echo '$(pane-spawn \"fake\" \"03:34:57 entry\")'"), [])

    def test_contract_must_be_inside_executable_substitution(self):
        self.assertTrue(guard.concrete_local_fix('echo "$(' + self.declaration + self.spawn + ')"'))
        self.assertFalse(guard.concrete_local_fix(self.declaration + 'echo "$(' + self.spawn + ')"'))

    def test_escaped_substitution_is_only_display_text(self):
        self.assertEqual(guard.pane_commands('echo "\\$(pane-spawn \'fake\' \'03:34:57 entry\')"'), [])

    def test_display_quotes_inside_substitution_cannot_create_action(self):
        command = "echo \"$(printf '%s' 'text\"; pane-spawn \"fake\" \"03:34:57 entry\"; echo \"')\""
        self.assertEqual(guard.pane_commands(command), [])

    def test_git_metadata_is_not_a_worktree_file(self):
        target = self.root / ".git" / "config"
        self.assertFalse(guard.concrete_local_fix(
            "FIX_ROUND_SCOPE=local-pr FIX_ROUND_TARGETS=" + shlex.quote(str(target)) + " " + self.spawn))

    def test_linked_worktree_file_is_accepted(self):
        subprocess.run(["git", "-C", str(self.root), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                        "commit", "--allow-empty", "-qm", "fixture"], check=True)
        linked = Path(self.tmp.name) / "linked"
        subprocess.run(["git", "-C", str(self.root), "worktree", "add", "-q", "--detach", str(linked)], check=True)
        target = linked / "known-fix.py"
        target.write_text("# worktree target\n")
        self.assertTrue(guard.concrete_local_fix(
            "FIX_ROUND_SCOPE=local-pr FIX_ROUND_TARGETS=" + shlex.quote(str(target)) + " " + self.spawn))

    def test_existing_file_outside_git_worktree_is_rejected(self):
        outside = Path(self.tmp.name) / "host-config"
        outside.write_text("# outside repository\n")
        for target in (outside, Path("/etc/hosts")):
            self.assertFalse(guard.concrete_local_fix(
                "FIX_ROUND_SCOPE=local-pr FIX_ROUND_TARGETS=" + shlex.quote(str(target)) + " " + self.spawn))

    def test_symlink_outside_git_worktree_is_rejected(self):
        outside = Path(self.tmp.name) / "host-config"
        outside.write_text("# outside repository\n")
        link = self.root / "linked-config"
        link.symlink_to(outside)
        self.assertFalse(guard.concrete_local_fix(
            "FIX_ROUND_SCOPE=local-pr FIX_ROUND_TARGETS=" + shlex.quote(str(link)) + " " + self.spawn))

    def test_symlink_into_another_repository_is_rejected(self):
        outside_repo = Path(self.tmp.name) / "other-repo"
        outside_repo.mkdir()
        subprocess.run(["git", "init", "-q", str(outside_repo)], check=True)
        outside = outside_repo / "other-file.py"
        outside.write_text("# another repository\n")
        link = self.root / "linked-file.py"
        link.symlink_to(outside)
        self.assertFalse(guard.concrete_local_fix(
            "FIX_ROUND_SCOPE=local-pr FIX_ROUND_TARGETS=" + shlex.quote(str(link)) + " " + self.spawn))

    def test_existing_untracked_file_in_git_worktree_is_accepted(self):
        self.assertTrue(guard.concrete_local_fix(self.declaration + self.spawn))


if __name__ == "__main__":
    unittest.main()
