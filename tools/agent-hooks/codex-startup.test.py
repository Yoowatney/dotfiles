#!/usr/bin/env python3
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('startup', Path(__file__).with_name('codex-startup.py'))
startup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(startup)


class StartupTest(unittest.TestCase):
    def test_only_explicit_worker_regions_are_removed(self):
        source = 'Safety\n<!-- pane:skip -->\nMain contact\n<!-- /pane:skip -->\n<!-- pane: Worker contract -->\nPermissions\n'
        self.assertEqual(startup.worker_rules(source), 'Safety\nWorker contract\nPermissions\n')
        with self.assertRaises(ValueError):
            startup.worker_rules('Safety\n<!-- pane:skip -->\nunfinished')

    def test_profiles_keep_unknown_and_required_skills(self):
        self.assertTrue(startup.keep_skill('code', Path('/skills/agent-reach/SKILL.md')))
        self.assertTrue(startup.keep_skill('code', Path('/skills/custom-safety/SKILL.md')))
        self.assertFalse(startup.keep_skill('code', Path('/skills/hyperframes-audio/SKILL.md')))
        self.assertTrue(startup.keep_skill('media', Path('/skills/hyperframes-audio/SKILL.md')))
        self.assertTrue(startup.keep_skill('full', Path('/skills/hyperframes-audio/SKILL.md')))

    def test_profile_keeps_canonical_home_and_never_redirects_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            source = home / '.codex'
            source.mkdir()
            (source / 'AGENTS.md').write_text('Safety\n<!-- pane:skip -->\nMain only\n<!-- /pane:skip -->\n')
            for name in ['auth.json', 'hooks.json', 'config.toml']:
                (source / name).write_text('' if name == 'config.toml' else '{}')
            (source / 'sessions').mkdir()
            env = {'HOME': str(home), 'HERDR_CODEX_STARTUP_ROLE': 'worker', 'HERDR_CODEX_TASK_TYPE': 'code'}
            plan = startup.prepare(env)
            self.assertNotIn('CODEX_HOME', plan['env'])
            self.assertEqual(sorted(p.name for p in source.iterdir()), ['AGENTS.md', 'auth.json', 'config.toml', 'hooks.json', 'sessions'])
            self.assertIn('Main only', (source / 'AGENTS.md').read_text())
            self.assertEqual(json.loads(Path(plan['manifest']).read_text())['task_type'], 'code')

    def test_youtube_skills_include_nested_installer_snapshots(self):
        for path in (Path('/skills/youtube-summary/SKILL.md'),
                     Path('/skills/youtube-summary-workspace/skill-snapshot/SKILL.md')):
            self.assertFalse(startup.keep_skill('code', path))
            self.assertTrue(startup.keep_skill('media', path))
            self.assertTrue(startup.keep_skill('full', path))
        self.assertTrue(startup.keep_skill('code', Path('/skills/unknown/skill-snapshot/SKILL.md')))
        self.assertTrue(startup.keep_skill('code', Path('/skills/youtube-summary-workspace/unknown-safety/SKILL.md')))
        self.assertTrue(startup.keep_skill('code', Path('/plugins/cache/youtube-summary-workspace/1.0/skills/unknown-safety/SKILL.md')))

    def test_main_checks_unknown_type_and_switch_use_original_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            for role, kind in [('main', 'code'), ('check', 'code'), ('worker', 'unknown')]:
                env = {'HOME': tmp, 'HERDR_CODEX_STARTUP_ROLE': role, 'HERDR_CODEX_TASK_TYPE': kind}
                self.assertFalse(startup.prepare(env)['active'])
            switch = Path(tmp) / '.local/state/codex-startup/mode'
            switch.parent.mkdir(parents=True)
            switch.write_text('full\n')
            self.assertFalse(startup.prepare({'HOME': tmp, 'HERDR_CODEX_STARTUP_ROLE': 'worker', 'HERDR_CODEX_TASK_TYPE': 'code'})['active'])

    def test_existing_skill_disables_survive_worker_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            source = home / '.codex'
            source.mkdir()
            (source / 'AGENTS.md').write_text('Safety\n')
            (source / 'config.toml').write_text('[skills]\nconfig=[{path="/custom/blocked/SKILL.md",enabled=false}]\n')
            skill = home / '.agents/skills/hyperframes/SKILL.md'
            skill.parent.mkdir(parents=True)
            skill.write_text('demo')
            plan = startup.prepare({'HOME': tmp, 'HERDR_CODEX_STARTUP_ROLE': 'worker', 'HERDR_CODEX_TASK_TYPE': 'code'})
            self.assertIn('/custom/blocked/SKILL.md', plan['args'][1])

    def test_custom_canonical_home_is_never_replaced_for_workers_or_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            source = home / '.codex'
            source.mkdir()
            (source / 'AGENTS.md').write_text('Safety\n<!-- pane:skip -->\nMain\n<!-- /pane:skip -->\n')
            base = {'HOME': tmp, 'HERDR_CODEX_STARTUP_ROLE': 'worker', 'HERDR_CODEX_TASK_TYPE': 'code'}
            for role, kind in [('worker', 'code'), ('check', 'code'), ('worker', 'full'), ('main', 'code')]:
                plan = startup.prepare(dict(base, CODEX_HOME=str(source), HERDR_CODEX_STARTUP_ROLE=role, HERDR_CODEX_TASK_TYPE=kind))
                self.assertEqual(Path(plan['env']['CODEX_HOME']).resolve(), source.resolve())
            switch = home / '.local/state/codex-startup/mode'
            switch.parent.mkdir(parents=True, exist_ok=True)
            switch.write_text('full\n')
            self.assertEqual(startup.prepare(dict(base, CODEX_HOME=str(source)))['env']['CODEX_HOME'], str(source))

    def test_memory_filter_removes_only_known_unrelated_links(self):
        text = '# Rules\nNever publish secrets.\n- [Video](hyperframes-guide.md) — video\n- [Safety](safety.md) — required\n'
        out = startup.memory_index(text, 'code')
        self.assertNotIn('[Video]', out)
        self.assertIn('Never publish secrets.', out)
        self.assertIn('[Safety]', out)
        self.assertEqual(startup.memory_index(text, 'full'), text)

    def test_project_or_profile_skill_overrides_retain_full_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            source = home / '.codex'
            source.mkdir()
            skill = home / '.agents/skills/hyperframes/SKILL.md'
            skill.parent.mkdir(parents=True)
            skill.write_text('demo')
            cwd = home / 'project'
            (cwd / '.codex').mkdir(parents=True)
            local = cwd / '.codex/config.toml'
            local.write_text('[skills]\nconfig=[{path="/project/blocked/SKILL.md",enabled=false}]\n')
            env = {'HOME': tmp, 'HERDR_CODEX_WORKDIR': str(cwd), 'HERDR_CODEX_STARTUP_ROLE': 'worker', 'HERDR_CODEX_TASK_TYPE': 'code'}
            self.assertEqual(startup.prepare(env)['args'], [])
            local.unlink()
            (source / 'config.toml').write_text('[profiles.special.skills]\nconfig=[{path="/profile/blocked/SKILL.md",enabled=false}]\n')
            self.assertEqual(startup.prepare(env)['args'], [])

    def test_unreadable_or_malformed_switch_keeps_full_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            switch = Path(tmp) / '.local/state/codex-startup/mode'
            switch.parent.mkdir(parents=True)
            for role in ('main', 'check', 'worker'):
                env = {'HOME': tmp, 'HERDR_CODEX_STARTUP_ROLE': role, 'HERDR_CODEX_TASK_TYPE': 'code'}
                switch.write_bytes(b'\xff')
                self.assertFalse(startup.policy(env)[2])
                switch.unlink()
                switch.mkdir()
                self.assertFalse(startup.policy(env)[2])
                switch.rmdir()


if __name__ == '__main__':
    unittest.main()
