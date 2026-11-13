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
            main = memory.context(cwd, dict(env, HERDR_CODEX_STARTUP_ROLE='main'))
            self.assertIn('Main contact', main)
            self.assertIn('[Video]', main)
            self.assertNotIn('Worker contract', main)  # the lead view: worker-only lines and markers drop
            self.assertNotIn('<!--', main)
            check = memory.context(cwd, dict(env, HERDR_CODEX_STARTUP_ROLE='check'))
            self.assertNotIn('Main contact', check)  # a check reads the worker view, the one the eval scores
            self.assertIn('Worker contract', check)
            self.assertIn('[Video]', check)  # its memory index keeps every entry: a check has no task type
            switch = home / '.local/state/codex-startup/mode'
            switch.parent.mkdir(parents=True)
            switch.write_text('full\n')
            self.assertIn('Main contact', memory.context(cwd, env))
            self.assertIn('[Video]', memory.context(cwd, env))

    def test_global_role_lines_follow_the_role_not_the_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve()
            cwd = home / 'discord'
            cwd.mkdir()
            codex = home / '.codex'
            codex.mkdir()
            full = 'Shared rule\n<!-- pane:skip -->\nLead owns its panes\n<!-- /pane:skip -->\n<!-- pane: Report to your lead -->\n'
            (codex / 'AGENTS.md').write_text(full)
            # Without an override Codex reads the whole AGENTS.md itself, so the hook adds nothing.
            self.assertNotIn('Lead owns its panes', memory.context(cwd, {'HOME': str(home)}))
            (codex / 'AGENTS.override.md').write_text('Shared rule\n')  # what activate installs: the shared lines only
            home_main = memory.context(cwd, {'HOME': str(home)})  # a Discord main on the home Mac: Codex, no role set
            self.assertIn('Lead owns its panes', home_main)
            self.assertNotIn('Report to your lead', home_main)
            for role in ('main', 'general', 'lead'):
                self.assertIn('Lead owns its panes', memory.context(cwd, {'HOME': str(home), 'HERDR_CODEX_STARTUP_ROLE': role}))
            worker = memory.context(cwd, {'HOME': str(home), 'HERDR_CODEX_STARTUP_ROLE': 'worker', 'HERDR_CODEX_TASK_TYPE': 'code'})
            self.assertIn('Report to your lead', worker)
            self.assertNotIn('Lead owns its panes', worker)
            check = memory.context(cwd, {'HOME': str(home), 'HERDR_CODEX_STARTUP_ROLE': 'check'})
            self.assertEqual(check, worker)
            switch = home / '.local/state/codex-startup/mode'
            switch.parent.mkdir(parents=True)
            switch.write_text('full\n')  # the full-context switch puts back every line the override lacks
            restored = memory.context(cwd, {'HOME': str(home), 'HERDR_CODEX_STARTUP_ROLE': 'worker', 'HERDR_CODEX_TASK_TYPE': 'code'})
            self.assertIn('Lead owns its panes', restored)
            self.assertIn('Report to your lead', restored)

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
