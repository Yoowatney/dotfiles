#!/usr/bin/env python3
"""The live checkouts that launchd jobs and every session read stay on main (2026-11-13: a worker switched one to a
feature branch and a launchd job ran its half-edited code). Exercises the real PreToolUse entry point."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HOOK = Path(__file__).with_name('no-sed-inplace.py')


def git(cwd, *args):
    subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@t', GIT_COMMITTER_NAME='t',
                            GIT_COMMITTER_EMAIL='t@t'))


class LiveCheckoutGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name).resolve()
        self.live = root / 'live'
        self.live.mkdir()
        git(self.live, 'init', '-q', '-b', 'main')
        (self.live / 'file.txt').write_text('x')
        (self.live / 'sub').mkdir()
        git(self.live, 'add', '.')
        git(self.live, 'commit', '-q', '-m', 'one')
        git(self.live, 'branch', 'feature')
        git(self.live, 'tag', 'v1')
        self.work = root / 'work'
        git(self.live, 'worktree', 'add', '-q', '-b', 'task', str(self.work))
        self.other = root / 'other'  # a checkout nothing reads
        self.other.mkdir()
        git(self.other, 'init', '-q', '-b', 'main')
        git(self.live, 'update-ref', 'refs/remotes/origin/remote-only', 'HEAD')  # fetched, no local branch yet
        git(self.live, 'checkout', '-q', 'feature')  # so that @{-1} is feature while live is on main
        git(self.live, 'checkout', '-q', 'main')
        self.back = root / 'back'  # on feature, whose previous branch is main
        self.back.mkdir()
        git(self.back, 'init', '-q', '-b', 'main')
        (self.back / 'f').write_text('x')
        git(self.back, 'add', '.')
        git(self.back, 'commit', '-q', '-m', 'one')
        git(self.back, 'checkout', '-q', '-b', 'feature')
        self.env = dict(os.environ, LIVE_CHECKOUTS=os.pathsep.join([str(self.live), str(self.back)]),
                        LIVE_ROOT=str(root))

    def decision(self, command, cwd):
        result = subprocess.run([sys.executable, '-I', str(HOOK)], text=True, capture_output=True, env=self.env,
                                timeout=10, input=json.dumps({'tool_name': 'Bash', 'cwd': str(cwd),
                                                              'tool_input': {'command': command}}))
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)['hookSpecificOutput'] if result.stdout.strip() else {}

    def denied(self, command, cwd=None):
        out = self.decision(command, cwd or self.live)
        self.assertEqual(out.get('permissionDecision'), 'deny', command)
        self.assertIn('live-checkout', out['permissionDecisionReason'], command)
        self.assertIn('git worktree add', out['permissionDecisionReason'], command)

    def allowed(self, command, cwd=None):
        self.assertEqual(self.decision(command, cwd or self.live), {}, command)

    def test_a_branch_change_in_a_live_checkout_is_denied(self):
        for command in ['git checkout -b x', 'git checkout -B x origin/main', 'git switch -c x', 'git switch -C x',
                        'git switch --create x', 'git checkout --orphan x', 'git switch feature', 'git checkout feature',
                        'git checkout -q feature', 'git checkout --detach', 'git switch --detach', 'git checkout -',
                        'git switch -', 'git checkout v1', 'git switch -f feature', 'git checkout --force feature',
                        'git fetch -q && git checkout -b x origin/main']:
            with self.subTest(command=command):
                self.denied(command)

    def test_the_directory_comes_from_cd_or_dash_c_as_well_as_the_hook_cwd(self):
        self.denied('git checkout -b x', self.live / 'sub')
        self.denied(f'cd {self.live} && git switch -c x', self.other)
        self.denied(f'git -C {self.live} checkout -b x', self.other)
        self.denied(f'git -C {self.live} switch feature', self.other)
        self.denied('git -C .. checkout -b x', self.live / 'sub')

    def test_forms_git_accepts_but_the_first_version_missed(self):
        for command in ['git checkout remote-only', 'git switch remote-only', 'git switch -- feature',
                        'git switch -cnew', 'git switch -Cnew', 'git checkout -bnew']:
            with self.subTest(command=command):
                self.denied(command)
        self.denied('git -C "$LIVE_ROOT/live" switch feature', self.other)
        self.denied('cd "$LIVE_ROOT/live" && git checkout -b x', self.other)
        self.denied(f'(cd {self.work} && git status); git switch feature')
        self.denied(f'( cd {self.work}; git status ) && git switch feature')

    def test_option_clusters_cd_options_and_a_group_that_leaves_the_live_directory(self):
        for command in ['git checkout -qb x', 'git switch -qc x', 'git checkout --conflict merge feature',
                        'git checkout -f -qB x']:
            with self.subTest(command=command):
                self.denied(command)
        self.denied(f'cd -- {self.live} && git switch feature', self.other)
        self.denied(f'cd -P {self.live} && git switch feature', self.other)
        self.denied(f'cd {self.live} && (cd {self.work} && git status); git switch feature', self.other)
        self.denied(f'cd {self.live} && bash -c "cd {self.work}"; git switch feature', self.other)

    def test_every_form_that_means_the_previous_branch_is_resolved_not_matched(self):
        for command in ['git switch -', 'git switch -- -', 'git checkout -', 'git checkout @{-1}', 'git switch @{-1}',
                        'git switch -f -', 'git checkout -f @{-1}', 'git switch -q -- -', 'git checkout -q -',
                        'git checkout @{-3}']:  # past the reflog it resolves to nothing, and nothing is refused
            with self.subTest(command=command):
                self.denied(command)  # live is on main and its previous branch is feature
        for command in ['git switch -', 'git switch -- -', 'git checkout -', 'git checkout @{-1}', 'git switch @{-1}']:
            with self.subTest(command=command):
                self.allowed(command, self.back)  # there it only goes back to main
        self.allowed('git checkout @{-2}')  # live: two switches back is main

    def test_a_branch_name_made_by_a_substitution_is_not_trusted(self):
        for command in ['git switch $(git rev-parse --abbrev-ref @{-1})', 'git checkout $(echo feature)',
                        'git checkout `echo feature`', 'git switch "$(echo feature)"', 'git switch "$SOME_BRANCH"']:
            with self.subTest(command=command):
                self.denied(command)

    def test_a_reflog_form_is_not_trusted_after_another_switch_in_the_same_command(self):
        self.denied('git checkout main && git checkout @{-2}')  # the first switch moves @{-2} to feature
        self.denied('git checkout main && git switch -')
        self.allowed('git checkout @{-2}')
        self.allowed('git checkout --pathspec-from-file="$(printf paths.txt)"')

    def test_a_checkout_whose_branch_name_is_wrapped_is_not_read_as_a_file(self):
        for command in ['git checkout "$(git rev-parse --abbrev-ref @{-1})"', 'git checkout "$(echo feature)"',
                        'git checkout "`echo feature`"', 'git checkout "$SOME_BRANCH"', 'git checkout $SOME_BRANCH',
                        'git checkout ${SOME_BRANCH:-feature}', "git checkout 'feature'", 'git checkout fea"ture"',
                        'git checkout "feature"', 'git checkout ""']:
            with self.subTest(command=command):
                self.denied(command)

    def test_an_alias_of_checkout_or_switch_is_expanded(self):
        git(self.live, 'config', 'alias.co', 'checkout')
        git(self.live, 'config', 'alias.sw', 'switch')
        git(self.live, 'config', 'alias.nb', 'checkout -b')
        git(self.live, 'config', 'alias.lg', 'log --oneline')
        git(self.live, 'config', 'alias.sh', '!git checkout feature')
        git(self.live, 'config', 'alias.hi', '!echo hi')
        for command in ['git co feature', 'git sw feature', 'git nb x', 'git co -b x', 'git sh',
                        'git -c alias.go=checkout go feature', 'git -c alias.go="checkout -b" go x',
                        'git -c alias.go=!git\\ checkout\\ feature go']:
            with self.subTest(command=command):
                self.denied(command)
        self.denied('git co main && git checkout @{-2}')  # an alias is a switch too, for the reflog forms
        for command in ['git co main', 'git sw main', 'git lg', 'git hi', 'git -c alias.go=log go']:
            with self.subTest(command=command):
                self.allowed(command)

    def test_the_repository_can_be_named_by_git_dir_and_work_tree(self):
        self.denied(f'git --git-dir={self.live}/.git --work-tree={self.live} checkout feature', self.other)
        self.denied(f'git --work-tree={self.live} --git-dir={self.live}/.git switch -c x', self.other)
        self.denied(f'git --git-dir {self.live}/.git switch feature', self.other)
        self.denied(f'env git checkout feature')
        self.denied(f'command git checkout feature')
        self.denied('bash -c "git checkout feature"')
        self.denied('eval "git checkout feature"')

    def test_git_dir_work_tree_and_alias_errors_do_not_hide_the_protected_one(self):
        self.denied(f'git --work-tree=. -C {self.live} checkout feature', self.other)
        self.denied(f'git --git-dir={self.other}/.git checkout feature')  # cwd is live: its files are overwritten
        self.denied(f'git --git-dir={self.live}/.git --work-tree={self.other} checkout feature', self.other)
        self.denied('git -c "alias.bad=\'" bad; git checkout feature')
        self.allowed('git status && git switch -', self.back)  # a read-only command does not move @{-1}
        self.allowed('git fetch -q && git log -1 && git checkout @{-1}', self.back)
        self.denied('git branch -m main moved && git switch -', self.back)

    def test_alias_lookup_follows_the_named_repository_and_leading_options(self):
        git(self.live, 'config', 'alias.co', 'checkout')
        git(self.live, 'config', 'alias.cx', '-c advice.detachedHead=false checkout')
        self.denied(f'git --git-dir={self.live}/.git --work-tree={self.live} co feature', self.other)
        self.denied('git cx feature')
        self.denied(f'git -C {self.live} cx -b x', self.other)
        self.allowed('git cx main')

    def test_an_explicit_git_dir_and_work_tree_leave_the_calling_directory_alone(self):
        self.allowed(f'git --git-dir={self.work}/.git --work-tree={self.work} checkout -b task2')

    def test_an_alias_chain_longer_than_the_limit_is_refused(self):
        self.denied('git -c alias.a=b -c alias.b=c -c alias.c=d -c alias.d=checkout a feature')
        self.denied('git -c alias.a=b -c alias.b=a a feature')  # a loop never ends
        self.denied('git -c alias.a=b -c alias.b=c -c alias.c=d -c alias.d=log a')  # unknown end: refused, not guessed
        self.allowed('git -c alias.a=b -c alias.b=c -c alias.c=log a')  # within the limit, and it is only a log

    def test_going_back_to_main_and_file_restores_are_allowed(self):
        for command in ['git checkout main', 'git switch main', 'git checkout -q main', 'git checkout -- file.txt',
                        'git checkout HEAD -- file.txt', 'git checkout feature -- file.txt', 'git checkout file.txt',
                        'git checkout .', 'git status', 'git branch x', 'git pull', 'git fetch origin main',
                        'git worktree add /tmp/x -b y origin/main', 'git commit -m "git checkout -b x"',
                        "echo 'git switch -c x'", 'git log --oneline']:
            with self.subTest(command=command):
                self.allowed(command)

    def test_a_worktree_and_an_unwatched_repo_are_free(self):
        for command in ['git checkout -b y', 'git switch -c y', 'git switch main', 'git checkout --detach']:
            with self.subTest(command=command):
                self.allowed(command, self.work)
                self.allowed(command, self.other)
        self.allowed(f'git -C {self.work} switch -c z', self.live)
        self.allowed(f'cd {self.work} && git checkout -b z', self.live)
        self.allowed(f'git -C {self.other} checkout -b z', self.live)

    def test_a_missing_directory_or_a_non_git_one_lets_the_command_run(self):
        self.allowed('git checkout -b x', self.tmp.name + '/nowhere')
        self.allowed(f'cd {self.tmp.name}/nowhere && git checkout -b x', self.other)


if __name__ == '__main__':
    unittest.main()
