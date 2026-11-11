#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import re
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('memory_hook', Path(__file__).with_name('codex-memory.py'))
memory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(memory)


class MemoryHookTest(unittest.TestCase):
    def test_worker_only_omits_marked_parent_rules_and_unrelated_index_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve()
            cwd = home / 'project'
            (cwd / '.git').mkdir(parents=True)
            (home / 'AGENTS.md').write_text('Parent safety\n<!-- pane:skip -->\nMain contact\n<!-- /pane:skip -->\n<!-- pane: Worker contract -->\n')
            (cwd / 'AGENTS.md').write_text('Project safety is auto-loaded\n')
            (cwd / 'CLAUDE.local.md').write_text('Local safety\n')
            directory = home / '.claude/projects' / re.sub('[^A-Za-z0-9]', '-', str(cwd)) / 'memory'
            directory.mkdir(parents=True)
            index = directory / 'MEMORY.md'
            index.write_text('Never publish secrets\n- [Video](hyperframes-guide.md)\n- [Safety](safety.md)\n')
            env = {'HOME': str(home), 'HERDR_CODEX_STARTUP_ROLE': 'worker', 'HERDR_CODEX_TASK_TYPE': 'code'}
            text = memory.context(cwd, env)
            self.assertIn('Parent safety', text)
            self.assertIn('Worker contract', text)
            self.assertIn('Local safety', text)
            self.assertIn('Never publish secrets', text)
            self.assertIn('[Safety]', text)
            self.assertIn(str(index), text)
            self.assertNotIn('Main contact', text)
            self.assertNotIn('[Video]', text)
            self.assertNotIn('Project safety is auto-loaded', text)
            for role in ('main', 'check'):
                full = memory.context(cwd, dict(env, HERDR_CODEX_STARTUP_ROLE=role))
                self.assertIn('Main contact', full)
                self.assertIn('[Video]', full)
            switch = home / '.local/state/codex-startup/mode'
            switch.parent.mkdir(parents=True)
            switch.write_text('full\n')
            self.assertIn('Main contact', memory.context(cwd, env))
            self.assertIn('[Video]', memory.context(cwd, env))

    def test_malformed_rule_boundary_keeps_the_whole_rule(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve()
            cwd = home / 'project'
            (cwd / '.git').mkdir(parents=True)
            (home / 'AGENTS.md').write_text('Safety\n<!-- pane:skip -->\nUnclosed safety\n')
            env = {'HOME': str(home), 'HERDR_CODEX_STARTUP_ROLE': 'worker', 'HERDR_CODEX_TASK_TYPE': 'code'}
            self.assertIn('Unclosed safety', memory.context(cwd, env))


if __name__ == '__main__':
    unittest.main()
