#!/usr/bin/env python3
"""Removing an MCP server table from Codex's config.toml stops every running Codex session that was started with a
-c mcp_servers.<name>... flag: its next config reload fails with "invalid transport" (2026-11-17, two check panes).
Exercises the real PreToolUse entry point, with a fake ps on PATH listing the processes."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

HOOK = Path(__file__).with_name('no-sed-inplace.py')
CONFIG = '[mcp_servers.slack]\ncommand = "npx"\n\n[mcp_servers.slack.env]\nX = "1"\n\n[mcp_servers.munice]\nurl = "https://x"\n'
CODEX = '/opt/x/vendor/bin/codex -c mcp_servers.slack.enabled_tools=["a"] -c mcp_servers.munice.enabled=false'
SHELL = '/bin/zsh -c echo mcp_servers.slack.enabled=false'  # not a Codex process


class McpRemovalGuardTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.home = self.root / 'codex'
        self.home.mkdir()
        self.config = self.home / 'config.toml'
        self.config.write_text(CONFIG)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.processes([CODEX, CODEX, SHELL])

    def processes(self, lines):
        (self.root / 'ps.txt').write_text(''.join(line + '\n' for line in lines))
        ps = self.bin / 'ps'
        ps.write_text(f'#!/bin/sh\ncat "{self.root / "ps.txt"}"\n')
        ps.chmod(0o755)

    def run_hook(self, tool, **tool_input):
        out = subprocess.run(['python3', '-I', str(HOOK)], input=json.dumps({'tool_name': tool, 'tool_input': tool_input}),
                             capture_output=True, text=True, timeout=30,
                             env=dict(os.environ, CODEX_HOME=str(self.home), PATH=f'{self.bin}:{os.environ["PATH"]}'))
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)['hookSpecificOutput']['permissionDecisionReason'] if out.stdout.strip() else None

    def assert_refused(self, reason, count=2):
        self.assertIsNotNone(reason)
        self.assertIn('codex-mcp-remove', reason)
        self.assertIn(f'{count}개', reason, reason)
        self.assertIn('끝난 뒤', reason)

    def test_refuses_codex_mcp_remove_while_a_session_carries_the_server(self):
        self.assert_refused(self.run_hook('Bash', command='codex mcp remove slack'))

    def test_refuses_an_edit_that_deletes_the_table(self):
        self.assert_refused(self.run_hook('Edit', file_path=str(self.config),
                                          old_string='[mcp_servers.slack]\ncommand = "npx"\n', new_string=''))
        self.assert_refused(self.run_hook('MultiEdit', file_path=str(self.config), edits=[
            {'old_string': '[mcp_servers.slack]\ncommand = "npx"\n', 'new_string': ''}]))

    def test_refuses_an_edit_that_leaves_the_table_without_command_or_url(self):
        self.assert_refused(self.run_hook('Edit', file_path=str(self.config), old_string='command = "npx"\n', new_string=''))

    def test_refuses_a_write_without_the_table(self):
        self.assert_refused(self.run_hook('Write', file_path=str(self.config),
                                          content='[mcp_servers.munice]\nurl = "https://x"\n'))

    def test_refuses_bash_that_rewrites_the_config(self):
        script = (f"python3 - <<'EOF'\nimport re\np = '{self.config}'\ns = open(p).read()\n"
                  "open(p, 'w').write(re.sub(r'\\[mcp_servers\\.slack\\][^\\[]*', '', s))\nEOF")
        self.assert_refused(self.run_hook('Bash', command=script))
        self.assert_refused(self.run_hook('Bash', command=f'cp /tmp/old.toml {self.config}'))  # the whole file goes
        self.assert_refused(self.run_hook('Bash', command='cat new.toml > "$HOME/.codex/config.toml"'))
        self.assert_refused(self.run_hook('Bash', command="bash -c 'cp /tmp/old.toml ~/.codex/config.toml'"))

    def test_allows_removal_when_no_codex_session_carries_the_server(self):
        self.processes([SHELL, '/opt/x/vendor/bin/codex -c mcp_servers.munice.enabled=false'])
        self.assertIsNone(self.run_hook('Bash', command='codex mcp remove slack'))
        self.assertIsNone(self.run_hook('Edit', file_path=str(self.config),
                                        old_string='[mcp_servers.slack]\ncommand = "npx"\n', new_string=''))

    def test_allows_reads_other_files_and_a_stub_that_keeps_the_table(self):
        self.assertIsNone(self.run_hook('Bash', command=f'grep -c slack {self.config}'))
        self.assertIsNone(self.run_hook('Bash', command='cat ~/.codex/config.toml'))  # secret-print-guard owns reads
        self.assertIsNone(self.run_hook('Bash', command=f'head -5 {self.config}'))
        self.assertIsNone(self.run_hook('Bash', command="git commit -m 'guard: refuse ~/.codex/config.toml table removal'"))
        self.assertIsNone(self.run_hook('Bash', command=(
            'git commit -m "$(cat <<\'EOF\'\nEdits of ~/.codex/config.toml are checked.\nEOF\n)"')))
        self.assertIsNone(self.run_hook('Edit', file_path=str(self.config), old_string='command = "npx"',
                                        new_string='command = "/usr/bin/false"\nenabled = false'))
        self.assertIsNone(self.run_hook('Edit', file_path=str(self.root / 'other.toml'),
                                        old_string='[mcp_servers.slack]\n', new_string=''))


if __name__ == '__main__':
    unittest.main()
