#!/usr/bin/env python3
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
import tempfile
import tomllib
import unittest

spec = importlib.util.spec_from_file_location('startup', Path(__file__).with_name('codex-startup.py'))
startup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(startup)


class AnalysisCheckTest(unittest.TestCase):
    def plan(self, home, **overrides):
        env = {**os.environ, 'HOME': str(home), 'CODEX_HOME': str(home / '.codex'),
               'XDG_STATE_HOME': str(home / 'state'), 'HERDR_CODEX_WORKDIR': str(home),
               'HERDR_CODEX_STARTUP_ROLE': 'check', 'HERDR_CODEX_STARTUP_ANALYSIS': '1', **overrides}
        result = subprocess.run([sys.executable, startup.__file__, '--plan'],
                                env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def connections(self, plan):
        config = {}
        for arg in plan['args']:
            if arg.startswith('mcp_servers.'):
                parsed = tomllib.loads(arg)['mcp_servers']
                for name, value in parsed.items():
                    config.setdefault(name, {}).update(value)
        return config

    def test_analysis_connections_are_read_only_in_fresh_open_and_full_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            for mode in ('fresh', 'open', 'full'):
                with self.subTest(mode=mode):
                    plan = self.plan(home, HERDR_CODEX_STARTUP_PROFILE='full' if mode == 'full' else '')
                    connections = self.connections(plan)
                    honeycomb, stripe = connections['honeycomb'], connections['stripe']
                    self.assertEqual(honeycomb['url'], 'https://mcp.honeycomb.io/mcp')
                    self.assertEqual(stripe['url'], 'https://mcp.stripe.com')
                    for server in (honeycomb, stripe):
                        self.assertTrue(server['enabled'])
                        self.assertEqual(set(server), {'url', 'enabled', 'enabled_tools'})
                        self.assertTrue(server['enabled_tools'])
                    self.assertIn('run_query', honeycomb['enabled_tools'])
                    self.assertIn('get_query_results', honeycomb['enabled_tools'])
                    self.assertIn('stripe_api_read', stripe['enabled_tools'])
                    self.assertIn('stripe_api_details', stripe['enabled_tools'])
                    for write in ('create_board', 'update_trigger', 'canvas_agent_invoke', 'feedback'):
                        self.assertNotIn(write, honeycomb['enabled_tools'])
                    for write in ('stripe_api_write', 'send_money', 'send_stripe_feedback', 'atlas_company_formation'):
                        self.assertNotIn(write, stripe['enabled_tools'])
            self.assertFalse((home / '.codex').exists())

    def test_bad_context_config_retains_read_only_policy_and_original_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config = home / '.codex/config.toml'
            config.parent.mkdir()
            original = '[invalid'
            config.write_text(original)
            connections = self.connections(self.plan(home))
            self.assertEqual(connections['stripe']['enabled_tools'].count('stripe_api_read'), 1)
            self.assertEqual(config.read_text(), original)

    @unittest.skipUnless(shutil.which('codex'), 'requires the Codex CLI')
    def test_real_cli_restricts_existing_http_and_rejects_stdio_transport(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config = home / '.codex/config.toml'
            config.parent.mkdir()
            original = ('[mcp_servers.stripe]\nurl="https://example.invalid/mcp"\n'
                        'enabled=false\nenabled_tools=["stripe_api_write"]\n')
            config.write_text(original)
            env = {**os.environ, 'HOME': tmp, 'CODEX_HOME': str(home / '.codex'),
                   'XDG_STATE_HOME': str(home / 'state'), 'HERDR_CODEX_WORKDIR': tmp,
                   'HERDR_CODEX_STARTUP_ROLE': 'check', 'HERDR_CODEX_STARTUP_ANALYSIS': '1'}
            for name in ('honeycomb', 'stripe'):
                result = subprocess.run([sys.executable, startup.__file__, shutil.which('codex'),
                                         'mcp', 'get', name, '--json'],
                                        cwd=tmp, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                server = json.loads(result.stdout)
                self.assertEqual(server['transport']['type'], 'streamable_http')
                self.assertEqual(server['transport']['url'], startup.CHECK_ANALYSIS_SERVERS[name][0])
                self.assertEqual(server['enabled_tools'], list(startup.CHECK_ANALYSIS_SERVERS[name][1]))
                self.assertTrue(server['enabled'])
                self.assertNotIn('command', server['transport'])
            self.assertEqual(config.read_text(), original)
            stdio = '[mcp_servers.stripe]\ncommand="fixture-command"\n'
            config.write_text(stdio)
            result = subprocess.run([sys.executable, startup.__file__, shutil.which('codex'),
                                     'mcp', 'get', 'stripe', '--json'],
                                    cwd=tmp, env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('url is not supported for stdio', result.stderr)
            self.assertEqual(config.read_text(), stdio)

    def test_non_analysis_checks_disable_connections_and_other_roles_are_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            for profile in ('', 'full'):
                connections = self.connections(self.plan(home, HERDR_CODEX_STARTUP_ANALYSIS='',
                                                          HERDR_CODEX_STARTUP_PROFILE=profile))
                self.assertFalse(connections['honeycomb']['enabled'])
                self.assertFalse(connections['stripe']['enabled'])
            for role in ('main', 'general', 'worker', ''):
                connections = self.connections(self.plan(home, HERDR_CODEX_STARTUP_ROLE=role))
                self.assertNotIn('honeycomb', connections)
                self.assertNotIn('stripe', connections)

    def test_policy_follows_caller_overrides_and_precedes_argument_separator(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            probe = home / 'probe'
            probe.write_text('#!' + sys.executable + '\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n')
            probe.chmod(0o700)
            env = {**os.environ, 'HOME': tmp, 'CODEX_HOME': str(home / '.codex'),
                   'XDG_STATE_HOME': str(home / 'state'), 'HERDR_CODEX_WORKDIR': tmp,
                   'HERDR_CODEX_STARTUP_ROLE': 'check', 'HERDR_CODEX_STARTUP_ANALYSIS': '1'}
            caller = 'mcp_servers.stripe.enabled_tools=["stripe_api_write"]'
            for tail in ([], ['--', 'literal prompt']):
                result = subprocess.run([sys.executable, startup.__file__, str(probe), '-c', caller, *tail],
                                        env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                args = json.loads(result.stdout)
                policy = next(a for a in args if a.startswith('mcp_servers.stripe={'))
                self.assertGreater(args.index(policy), args.index(caller))
                if tail:
                    self.assertLess(args.index(policy), args.index('--'))
                    self.assertEqual(args[-2:], ['--', 'literal prompt'])


class StartupTest(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'darwin', 'requires macOS Seatbelt')
    def test_check_process_tree_is_read_only_even_with_yolo(self):
        with tempfile.TemporaryDirectory() as box:
            home = Path(box)
            target = home / '한글 대상 "quoted"'
            target.mkdir()
            (target / 'existing').write_text('original')
            link = home / 'target-link'
            link.symlink_to(target, target_is_directory=True)
            probe = home / 'probe.py'
            probe.write_text('#!' + sys.executable + '\n' + """import os, pathlib, subprocess, socket
p = pathlib.Path(os.environ['HERDR_CHECK_TARGET'])
assert (p / 'existing').read_text() == 'original'
for name in ['new', 'existing']:
    try: (p / name).write_text('changed')
    except PermissionError: pass
    else: raise AssertionError('direct write escaped')
for command in ['printf changed > "$HERDR_CHECK_TARGET/shell"', '/bin/ps -p 1 > "$HERDR_CHECK_TARGET/ps"']:
    assert subprocess.run(['/bin/sh', '-c', command], stderr=subprocess.DEVNULL).returncode != 0
assert subprocess.check_output(['/bin/ps', '-p', '1', '-o', 'pid=']).strip() == b'1'
pathlib.Path(os.environ['CHECK_SCRATCH']).write_text('allowed')
with socket.socket() as server: server.bind(('127.0.0.1', 0))
print('protected')
""")
            probe.chmod(0o700)
            env = {**os.environ, 'HOME': box, 'CODEX_HOME': str(home / '.codex'),
                   'XDG_STATE_HOME': str(home / 'state'), 'HERDR_CHECK_TARGET': str(link),
                   'CHECK_SCRATCH': str(home / 'scratch'), 'HERDR_CODEX_STARTUP_ROLE': 'check'}
            for analysis in ('', '1'):
                env['HERDR_CODEX_STARTUP_ANALYSIS'] = analysis
                for session in ('fresh', 'open'):
                    result = subprocess.run([sys.executable, str(Path(startup.__file__)), str(probe), '--yolo'], env=env, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('protected', result.stdout)
            self.assertEqual((target / 'existing').read_text(), 'original')
            env['HERDR_CHECK_TARGET'] = str(home / 'missing')
            result = subprocess.run([sys.executable, str(Path(startup.__file__)), sys.executable,
                                     '-c', 'print("unsafe")'], env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('unsafe', result.stdout)

    def test_launch_transport_preserves_argv_and_consumes_environment(self):
        payload = ['--sandbox', 'workspace-write', '-c', 'value="with spaces"', '--model', 'example']
        env = {'HERDR_CODEX_LAUNCH_ARGS': json.dumps(payload)}
        self.assertEqual(startup.launch_arguments(['--no-daemon', '--pane-spawn-args'], env), ['--no-daemon', *payload])
        self.assertNotIn('HERDR_CODEX_LAUNCH_ARGS', env)
        env = {'HERDR_CODEX_LAUNCH_ARGS': json.dumps(payload)}
        self.assertEqual(startup.launch_arguments(['--help'], env), ['--help'])
        self.assertNotIn('HERDR_CODEX_LAUNCH_ARGS', env)

    def test_launch_transport_fails_closed_on_missing_or_invalid_data(self):
        for encoded in (None, '', '{}', '[]', '[1]', '["bad\\nargument"]'):
            with self.subTest(encoded=encoded), self.assertRaises(SystemExit):
                startup.launch_arguments(['--pane-spawn-args'], {'HERDR_CODEX_LAUNCH_ARGS': encoded})
        with self.assertRaises(SystemExit):
            startup.launch_arguments(['--pane-spawn-args', '--pane-spawn-args'], {'HERDR_CODEX_LAUNCH_ARGS': '["--help"]'})

    def test_only_explicit_worker_regions_are_removed(self):
        source = 'Safety\n<!-- pane:skip -->\nMain contact\n<!-- /pane:skip -->\n<!-- pane: Worker contract -->\nPermissions\n'
        self.assertEqual(startup.worker_rules(source), 'Safety\nWorker contract\nPermissions\n')
        self.assertEqual(startup.lead_rules(source), 'Safety\nMain contact\nPermissions\n')
        self.assertEqual(startup.pane_view(source, 'shared'), 'Safety\nPermissions\n')
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
            for role, kind in [('unknown', 'code'), ('worker', 'unknown')]:
                env = {'HOME': tmp, 'HERDR_CODEX_STARTUP_ROLE': role, 'HERDR_CODEX_TASK_TYPE': kind}
                self.assertFalse(startup.prepare(env)['active'])
            switch = Path(tmp) / '.local/state/codex-startup/mode'
            switch.parent.mkdir(parents=True)
            switch.write_text('full\n')
            self.assertFalse(startup.prepare({'HOME': tmp, 'HERDR_CODEX_STARTUP_ROLE': 'worker', 'HERDR_CODEX_TASK_TYPE': 'code'})['active'])

    def test_general_main_and_check_profiles_restore_without_changing_safety(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / '.codex'
            source.mkdir()
            (source / 'config.toml').write_text('\n'.join('[mcp_servers.' + name + ']\ncommand="fixture"' for name in ('rea', 'Amplitude', 'notion-token', 'obsidian')))
            for role in ('general', 'main', 'check'):
                env = {'HOME': tmp, 'HERDR_CODEX_STARTUP_ROLE': role, 'HERDR_CODEX_TASK_TYPE': 'code'}
                plan = startup.prepare(env)
                self.assertTrue(plan['active'])
                self.assertEqual(plan['env'], env)
                self.assertIn('skills.max_context_tokens=2500', plan['args'])
                for name in ('rea', 'Amplitude', 'notion-token', 'obsidian'):
                    self.assertIn('mcp_servers.' + name + '.enabled=false', plan['args'])
                for name in ('figma', 'discord'):
                    self.assertIn('plugins.' + name + '@claude-plugins-official.enabled=false', plan['args'])
                self.assertFalse(any('hooks' in arg or 'approval' in arg or 'sandbox' in arg for arg in plan['args']))
                self.assertEqual(startup.prepare(dict(env, HERDR_CODEX_STARTUP_PROFILE='full'))['args'], [])

    def test_analysis_checks_keep_configured_amplitude_without_changing_other_roles(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / '.codex'
            source.mkdir()
            config = source / 'config.toml'
            original = '\n'.join('[mcp_servers.' + name + ']\ncommand="fixture"' for name in ('rea', 'Amplitude', 'obsidian'))
            config.write_text(original)
            for session in ('fresh', 'open'):
                env = {'HOME': tmp, 'HERDR_CODEX_STARTUP_ROLE': 'check', 'HERDR_CODEX_STARTUP_ANALYSIS': '1'}
                plan = startup.prepare(env)
                self.assertNotIn('mcp_servers.Amplitude.enabled=false', plan['args'])
                self.assertIn('mcp_servers.rea.enabled=false', plan['args'])
                self.assertIn('mcp_servers.obsidian.enabled=false', plan['args'])
                self.assertEqual(config.read_text(), original)
            for role in ('general', 'main'):
                self.assertIn('mcp_servers.Amplitude.enabled=false', startup.prepare(dict(env, HERDR_CODEX_STARTUP_ROLE=role))['args'])

    def test_workers_preserve_pre_compaction_mcp_and_plugin_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / '.codex'
            source.mkdir()
            config = source / 'config.toml'
            original = '\n'.join('[mcp_servers.' + name + ']\ncommand="fixture"' for name in
                                 ('rea', 'Amplitude', 'notion-token', 'obsidian', 'custom-server'))
            original += '\n[plugins."figma@claude-plugins-official"]\nenabled=true\n'
            original += '\n[plugins."discord@claude-plugins-official"]\nenabled=false\n'
            config.write_text(original)
            for kind in ('research', 'code'):
                with self.subTest(kind=kind):
                    env = {'HOME': tmp, 'HERDR_CODEX_WORKDIR': tmp,
                           'HERDR_CODEX_STARTUP_ROLE': 'worker', 'HERDR_CODEX_TASK_TYPE': kind}
                    plan = startup.prepare(env)
                    self.assertTrue(plan['active'])
                    self.assertEqual(plan['env'], env)
                    self.assertIn('skills.max_context_tokens=2500', plan['args'])
                    self.assertFalse(any(arg.startswith(('mcp_servers.', 'plugins.')) for arg in plan['args']))
                    self.assertEqual(config.read_text(), original)
                    self.assertEqual(startup.prepare(dict(env, HERDR_CODEX_STARTUP_PROFILE='full'))['args'], [])

    def test_absent_servers_do_not_create_invalid_transport_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = startup.prepare({'HOME': tmp, 'HERDR_CODEX_WORKDIR': tmp, 'HERDR_CODEX_STARTUP_ROLE': 'general'})
            self.assertFalse(any(arg.startswith('mcp_servers.') for arg in plan['args']))

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

    def test_name_based_skill_entries_survive_worker_filtering(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            source = home / '.codex'
            source.mkdir()
            (source / 'config.toml').write_text('[skills]\nconfig=[{name="custom-safety",enabled=false}]\n')
            skill = home / '.agents/skills/hyperframes/SKILL.md'
            skill.parent.mkdir(parents=True)
            skill.write_text('demo')
            plan = startup.prepare({'HOME': tmp, 'HERDR_CODEX_STARTUP_ROLE': 'worker', 'HERDR_CODEX_TASK_TYPE': 'code'})
            self.assertIn('name="custom-safety"', plan['args'][1])

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
            self.assertFalse(any(x.startswith('skills.config=') for x in startup.prepare(env)['args']))
            local.unlink()
            (source / 'config.toml').write_text('[profiles.special.skills]\nconfig=[{path="/profile/blocked/SKILL.md",enabled=false}]\n')
            self.assertFalse(any(x.startswith('skills.config=') for x in startup.prepare(env)['args']))

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
