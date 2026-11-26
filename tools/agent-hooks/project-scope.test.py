#!/usr/bin/env python3
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('project_scope', Path(__file__).with_name('project-scope.py'))
scope = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scope)


def put(path, plugins, name='settings.json'):
    (path / '.claude').mkdir(parents=True, exist_ok=True)
    (path / '.claude' / name).write_text(json.dumps({'enabledPlugins': plugins}))


class ProjectScopeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name).resolve() / 'home'
        self.team = self.home / 'work/team'
        self.repo = self.team / 'repo'
        (self.repo / '.git/worktrees/wt').mkdir(parents=True)
        put(self.team, {'team@x': True, 'near@x': False})
        put(self.repo, {'near@x': True})
        (self.team / 'CLAUDE.local.md').write_text('Team rules\n')

    def worktree(self, path):
        path.mkdir(parents=True)
        (path / '.git').write_text('gitdir: ' + str(self.repo / '.git/worktrees/wt') + '\n')
        return path

    def test_a_subfolder_inherits_and_the_nearer_folder_wins(self):
        sub = self.repo / 'src'
        sub.mkdir()
        self.assertEqual(scope.inherited(sub, self.home), {'team@x': True, 'near@x': True})
        self.assertEqual(scope.local_rules(sub, self.home), [])  # its own parents already load them

    def test_a_worktree_elsewhere_inherits_through_its_main_checkout(self):
        wt = self.worktree(self.home / 'work/team-wt/job')
        (wt / 'src').mkdir()
        for cwd in (wt, wt / 'src'):
            self.assertEqual(scope.inherited(cwd, self.home), {'team@x': True, 'near@x': True}, cwd)
            self.assertEqual(scope.local_rules(cwd, self.home), [self.team / 'CLAUDE.local.md'], cwd)

    def test_a_worktree_outside_home_still_finds_its_project(self):
        wt = self.worktree(Path(self.tmp.name).resolve() / 'elsewhere/job')
        self.assertEqual(scope.inherited(wt, self.home), {'team@x': True, 'near@x': True})

    def test_the_folder_s_own_settings_are_left_to_claude(self):
        sub = self.repo / 'src'
        put(sub, {'team@x': False}, 'settings.local.json')
        self.assertEqual(scope.inherited(sub, self.home), {'near@x': True})

    def test_a_parent_s_local_settings_stay_in_that_folder(self):
        put(self.team, {'mine@x': True}, 'settings.local.json')
        sub = self.repo / 'src'
        sub.mkdir()
        self.assertNotIn('mine@x', scope.inherited(sub, self.home))

    def test_nothing_outside_the_project_inherits(self):
        other = self.home / 'work/other'
        other.mkdir(parents=True)
        put(self.home, {'home@x': True})  # the user's own settings are not a project
        put(Path(self.tmp.name).resolve(), {'planted@x': True})
        self.assertEqual(scope.inherited(other, self.home), {})
        self.assertEqual(scope.inherited(Path(self.tmp.name).resolve() / 'x', self.home), {})

    def test_the_old_harness_version_turns_it_off(self):
        wt = self.worktree(self.home / 'work/team-wt/job')
        marker = self.home / '.config/agent-harness/version'
        marker.parent.mkdir(parents=True)
        marker.write_text('v0.1.0\n')
        self.assertEqual(scope.inherited(wt, self.home), {})
        self.assertEqual(scope.local_rules(wt, self.home), [])
        marker.write_text('v0.1.1\n')
        self.assertTrue(scope.inherited(wt, self.home))

    def test_broken_files_do_not_stop_a_launch(self):
        sub = self.repo / 'src'
        (sub / '.claude').mkdir(parents=True)
        (sub / '.claude/settings.local.json').write_text('[1]')
        (self.repo / '.claude/settings.json').write_text('{broken')
        self.assertEqual(scope.inherited(sub, self.home), {'team@x': True, 'near@x': False})


if __name__ == '__main__':
    unittest.main()
