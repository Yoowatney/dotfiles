#!/usr/bin/env python3
"""Real Git integration: merged personal PRs refresh only safe live checkouts."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent


class MergePullTest(unittest.TestCase):
    def test_real_remote_refresh_and_failure_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            env = dict(os.environ, HOME=tmp, GIT_CONFIG_NOSYSTEM="1",
                       GIT_CONFIG_GLOBAL=str(home / ".gitconfig"))

            def git(path, *args):
                return subprocess.check_output(
                    ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                     "-c", "commit.gpgsign=false", "-C", str(path), *args],
                    env=env, text=True, stderr=subprocess.DEVNULL).strip()

            remote = home / "remote.git"
            git(home, "init", "--bare", "--initial-branch=main", str(remote))
            seed = home / "seed"
            git(home, "clone", str(remote), str(seed))
            (seed / "value").write_text("before")
            git(seed, "add", "value")
            git(seed, "commit", "-m", "initial")
            git(seed, "push", "origin", "main")
            # Each GitHub URL rewrites to the same disposable bare remote.
            (home / ".gitconfig").write_text(
                '[url "' + str(remote) + '"]\n'
                '    insteadOf = https://github.com/Yoowatney/dotfiles.git\n'
                '    insteadOf = https://github.com/Yoowatney/munice-claude.git\n')
            for name, relative in [("dotfiles", ".dotfiles"),
                                   ("munice-claude", "work/munice/munice-claude")]:
                live = home / relative
                live.parent.mkdir(parents=True, exist_ok=True)
                git(home, "clone", "https://github.com/Yoowatney/" + name + ".git", str(live))

            (seed / "value").write_text("after")
            git(seed, "commit", "-am", "remote update")
            git(seed, "push", "origin", "main")

            def hook(command, cwd=None, response=None):
                payload = dict(hook_event_name="PostToolUse", tool_name="Bash",
                               cwd=str(cwd or home), tool_input=dict(command=command),
                               tool_response=response or dict(stdout="", stderr="", interrupted=False))
                result = subprocess.run(["python3", str(HERE / "post-merge-pull.py")],
                                        input=json.dumps(payload), text=True, capture_output=True, env=env)
                self.assertEqual(result.returncode, 0, result.stderr)
                return result.stdout

            live = home / ".dotfiles"
            before = git(live, "rev-parse", "HEAD")
            for command, response in [
                ("gh pr merge 1 -R Other/repo", None),
                ("echo gh pr merge 1 -R Yoowatney/dotfiles", None),
                ("gh pr merge 1 -R Yoowatney/dotfiles --auto", None),
                ("gh pr merge 1 -R Yoowatney/dotfiles; true", None),
                ("gh pr merge 1 -R Yoowatney/dotfiles", dict(exit_code=1)),
            ]:
                hook(command, response=response)
                self.assertEqual(git(live, "rev-parse", "HEAD"), before)
            git(live, "checkout", "-b", "work")
            self.assertIn("warning", hook("gh pr merge 1 -R Yoowatney/dotfiles").lower())
            self.assertEqual(git(live, "rev-parse", "HEAD"), before)
            git(live, "checkout", "main")
            (live / "local").write_text("uncommitted")
            hook("gh pr merge 1 --squash", cwd=live)
            self.assertEqual((live / "value").read_text(), "after")
            self.assertEqual((live / "local").read_text(), "uncommitted")
            (live / "local").unlink()
            private = home / "work/munice/munice-claude"
            hook("gh pr merge https://github.com/Yoowatney/munice-claude/pull/2 --squash")
            self.assertEqual((private / "value").read_text(), "after")
            # Divergence must warn without touching HEAD or working files.
            (live / "local").write_text("keep")
            git(live, "add", "local")
            git(live, "commit", "-m", "local change")
            (seed / "value").write_text("later")
            git(seed, "commit", "-am", "another remote update")
            git(seed, "push", "origin", "main")
            before = git(live, "rev-parse", "HEAD")
            self.assertIn("warning", hook("gh pr merge 3 --repo=Yoowatney/dotfiles").lower())
            self.assertEqual(git(live, "rev-parse", "HEAD"), before)
            self.assertEqual((live / "value").read_text(), "after")
            self.assertEqual((live / "local").read_text(), "keep")
            self.assertEqual(git(live, "status", "--porcelain"), "")

    def test_installer_preserves_safety_hooks_without_codex_posttooluse(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config = home / ".claude/settings.json"
            config.parent.mkdir()
            original = dict(matcher="Bash", hooks=[dict(type="command", command="safety")])
            config.write_text(json.dumps(dict(hooks=dict(PostToolUse=[original]))))
            for _ in range(2):
                subprocess.run(["python3", str(HERE / "install-codex-startup.py")],
                               env=dict(os.environ, HOME=tmp), check=True, capture_output=True)
            groups = json.loads(config.read_text())["hooks"]["PostToolUse"]
            self.assertEqual(groups[0], original)
            self.assertEqual(len(groups), 2)
            self.assertEqual(groups[1]["matcher"], "Bash")
            command = groups[1]["hooks"][0]["command"]
            result = subprocess.run(command, shell=True, input='{}', text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            codex = json.loads((home / ".codex/hooks.json").read_text())
            self.assertNotIn("PostToolUse", codex["hooks"])


if __name__ == "__main__":
    unittest.main()
