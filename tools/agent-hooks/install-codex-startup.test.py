#!/usr/bin/env python3
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent


class InstallTest(unittest.TestCase):
    def test_idempotent_preserves_guards_and_replaces_only_memory_hook(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '.codex/hooks.json'
            path.parent.mkdir()
            path.write_text(json.dumps({'hooks': {'SessionStart': [{'hooks': [
                {'type': 'command', 'command': 'bash /private/claude-memory.sh', 'additionalContextLimit': 60000},
                {'type': 'command', 'command': 'safety-guard'}]}],
                'PreToolUse': [{'hooks': [{'type': 'command', 'command': 'permission-guard'}]}]}}))
            for _ in range(2):
                result = subprocess.run(['python3', str(HERE / 'install-codex-startup.py')], env=dict(os.environ, HOME=tmp), capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(path.read_text())
            entries = [h for g in data['hooks']['SessionStart'] for h in g['hooks']]
            self.assertEqual(sum('codex-memory.py' in h['command'] for h in entries), 1)
            self.assertIn('safety-guard', [h['command'] for h in entries])
            self.assertEqual(data['hooks']['PreToolUse'][0]['hooks'][0]['command'], 'permission-guard')
            for event in ['SessionStart', 'Stop']:
                commands = [h['command'] for g in data['hooks'][event] for h in g['hooks']]
                self.assertEqual(sum('task-ledger.py hook ' + event in c for c in commands), 1)


if __name__ == '__main__':
    unittest.main()
