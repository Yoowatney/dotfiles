#!/usr/bin/env python3
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent


class InstallTest(unittest.TestCase):
    def test_reinstall_preserves_other_hooks_and_registers_one_startup(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".codex").mkdir()
            config = home / ".codex/hooks.json"
            config.write_text(json.dumps(dict(hooks=dict(SessionStart=[dict(hooks=[dict(type="command", command="existing")])]))))
            for _ in range(2):
                result = subprocess.run(["python3", str(HERE / "install-handoff.py")], env=dict(os.environ, HOME=tmp), capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            hooks = json.loads(config.read_text())["hooks"]["SessionStart"]
            commands = [h["command"] for group in hooks for h in group["hooks"]]
            self.assertIn("existing", commands)
            self.assertEqual(sum("handoff session codex" in c for c in commands), 1)
            result = subprocess.run(["python3", str(HERE / "install-handoff.py"), "--remove"], env=dict(os.environ, HOME=tmp), capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            hooks = json.loads(config.read_text())["hooks"]["SessionStart"]
            self.assertEqual([h["command"] for group in hooks for h in group["hooks"]], ["existing"])

    def test_bad_config_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / ".codex/hooks.json"
            config.parent.mkdir()
            config.write_text("invalid")
            result = subprocess.run(["python3", str(HERE / "install-handoff.py")], env=dict(os.environ, HOME=tmp), capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(config.read_text(), "invalid")


if __name__ == "__main__":
    unittest.main()
