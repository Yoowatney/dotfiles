#!/usr/bin/env python3
"""Process-local, reversible startup context for explicitly selected roles.

Catalog and connection visibility are role-scoped. Model, contact routing,
check sandbox, read-only restrictions and approval policy are retained.
All generated context and manifests stay outside the public repository.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import sys
try:
    import tomllib
except ImportError:  # Older system Python retains the full skill configuration.
    tomllib = None

TYPES = {'code', 'research', 'media', 'office'}
MEDIA = {'imagegen', 'asciinema-subtitles', 'media-use', 'youtube-summary', 'youtube-summary-workspace'}
OFFICE = {'documents', 'pdf', 'presentations', 'spreadsheets', 'google-docs', 'google-drive',
          'google-drive-comments', 'google-sheets', 'google-slides', 'template-creator'}
PERSONAL = {'closing-household-ledger', 'create-pet', 'pets', 'update-pet', 'main-orchestrator'}


def keep_skill(kind, path):
    if kind not in TYPES:
        return True
    name = path.parent.name
    # This installer stores its skill below a snapshot directory, not its name.
    if name == 'skill-snapshot' and path.parent.parent.name == 'youtube-summary-workspace':
        name = 'youtube-summary-workspace'
    if name in PERSONAL or name.startswith('notion-'):
        return False
    if kind != 'media' and (name.startswith('hyperframes') or name in MEDIA):
        return False
    if kind != 'office' and name in OFFICE:
        return False
    # Unknown skills and every cross-cutting safety/workflow skill remain visible.
    return True


def pane_view(text, *kinds):
    """Lines of the given kinds: 'shared', 'lead' (inside a pane:skip block) or 'worker' (a `<!-- pane: … -->` line)."""
    output, skipping = [], False
    for line in text.splitlines(keepends=True):
        if line.strip() == '<!-- pane:skip -->':
            if skipping:
                raise ValueError('nested worker rule marker')
            skipping = True
        elif line.strip() == '<!-- /pane:skip -->':
            if not skipping:
                raise ValueError('unmatched worker rule marker')
            skipping = False
        else:
            match = None if skipping else re.fullmatch(r'\s*<!-- pane: (.*?) -->\s*', line)
            kind = 'lead' if skipping else 'worker' if match else 'shared'
            if kind in kinds:
                output.append(match[1] + '\n' if match else line)
    if skipping:
        raise ValueError('unclosed worker rule marker')
    return ''.join(output)


def worker_rules(text):
    return pane_view(text, 'shared', 'worker')


def lead_rules(text):
    return pane_view(text, 'shared', 'lead')


def rules_view(env):
    """Rules follow the role, not the runtime: a worker or check pane reads the worker view, any other session
    (main, lead, general, or no role such as the home Discord main) the lead view; the full switch keeps all."""
    if not compact_mode(env):
        return 'full'
    return 'worker' if env.get('HERDR_CODEX_STARTUP_ROLE') in {'worker', 'check'} else 'lead'


def memory_index(text, kind):
    """Omit only ordinary link entries explicitly naming an excluded domain."""
    if kind not in TYPES:
        return text
    output = []
    for line in text.splitlines(keepends=True):
        match = re.match(r'^- \[[^\]]+\]\(([^)]+)\)', line)
        if match:
            stem = Path(match[1]).stem
            if any(stem == name or stem.startswith(name + '-') for name in MEDIA | OFFICE | PERSONAL):
                if not keep_skill(kind, Path('/skills') / stem / 'SKILL.md'):
                    continue
            if stem.startswith('hyperframes') and kind != 'media':
                continue
        output.append(line)
    return ''.join(output)


def compact_mode(env):
    home = Path(env.get('HOME', str(Path.home())))
    switch = Path(env.get('XDG_STATE_HOME', str(home / '.local/state'))) / 'codex-startup/mode'
    try:
        mode = switch.read_text().strip() if switch.exists() else 'compact'
    except (OSError, UnicodeError):
        mode = 'full'
    return mode == 'compact' and env.get('HERDR_CODEX_STARTUP_PROFILE') != 'full'


def policy(env):
    home = Path(env.get('HOME', str(Path.home())))
    kind = env.get('HERDR_CODEX_TASK_TYPE', 'full')
    return home, kind, env.get('HERDR_CODEX_STARTUP_ROLE') == 'worker' and kind in TYPES and compact_mode(env)


def has_skill_config(value):
    if isinstance(value, dict):
        return 'skills' in value or any(has_skill_config(v) for v in value.values())
    return isinstance(value, list) and any(has_skill_config(v) for v in value)


def inline_toml(value):
    if isinstance(value, dict):
        return '{' + ','.join(k + '=' + inline_toml(v) for k, v in value.items()) + '}'
    if isinstance(value, list):
        return '[' + ','.join(inline_toml(v) for v in value) + ']'
    return json.dumps(value)


def prepare(env):
    env = dict(env)
    home, kind, active = policy(env)
    result = {'active': False, 'env': env, 'args': [], 'task_type': kind}
    selected = env.get('HERDR_CODEX_STARTUP_ROLE') in {'main', 'general', 'check'} or active
    if not selected or not compact_mode(env):
        return result
    source = Path(env.get('CODEX_HOME', str(home / '.codex'))).resolve()
    state = Path(env.get('XDG_STATE_HOME', str(home / '.local/state'))) / 'codex-startup'
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    excluded = set()
    roots = [home / '.agents/skills', source / 'skills', source / 'plugins/cache']
    for root in roots:
        if root.exists():
            for path in root.rglob('SKILL.md'):
                if active and not keep_skill(kind, path):
                    excluded.update((str(path), str(path.resolve())))
    # pathlib does not recurse through symlinked skill directories.
    user_skills = home / '.agents/skills'
    if user_skills.exists():
        for child in user_skills.iterdir():
            path = child / 'SKILL.md'
            if active and path.exists() and not keep_skill(kind, path):
                excluded.update((str(path), str(path.resolve())))
    existing = []
    configured_servers = set()
    config = source / 'config.toml'
    reason = None
    if config.exists():
        if tomllib is None:
            excluded.clear()
        else:
            data = tomllib.loads(config.read_text())
            configured_servers.update(data.get('mcp_servers', {}))
            existing = data.get('skills', {}).get('config', [])
            if has_skill_config({k: v for k, v in data.items() if k != 'skills'}):
                reason = 'profile skill configuration retained'
    cwd = Path(env.get('HERDR_CODEX_WORKDIR', os.getcwd())).absolute()
    for parent in [cwd, *cwd.parents]:
        local = parent / '.codex/config.toml'
        if local.is_file() and local.resolve() != config.resolve():
            local_data = tomllib.loads(local.read_text()) if tomllib else {}
            configured_servers.update(local_data.get('mcp_servers', {}))
            if tomllib is None or has_skill_config(local_data):
                reason = 'project skill configuration retained'
    if reason:
        excluded.clear()
    # CLI arrays replace rather than append. Preserve every existing skill entry.
    entries = [inline_toml(entry) for entry in existing]
    entries += ['{path=' + json.dumps(p) + ',enabled=false}' for p in sorted(excluded)]
    args = ['-c', 'skills.config=[' + ','.join(entries) + ']'] if excluded else []
    # CLI overrides apply only to this process; a full session uses the original
    # connections. Keep hooks, approval policy, sandbox and credentials intact.
    args += ['-c', 'skills.max_context_tokens=2500']
    # Delegated workers retain the original connection scope, including research
    # integrations. Before compaction they applied no MCP or plugin overrides.
    if env.get('HERDR_CODEX_STARTUP_ROLE') != 'worker':
        for name in ('rea', 'Amplitude', 'notion-token', 'obsidian'):
            if name in configured_servers:
                # Codex CLI override paths split on dots; quotes become literal names.
                args += ['-c', 'mcp_servers.' + name + '.enabled=false']
        for plugin in ('figma', 'discord'):
            args += ['-c', 'plugins.' + plugin + '@claude-plugins-official.enabled=false']
    manifest = {'source_home': str(source), 'task_type': kind, 'global_rules': 'unchanged', 'catalog_fallback': reason,
                'disabled_skill_paths': sorted(excluded)}
    key = hashlib.sha256(str(source).encode()).hexdigest()[:12]
    path = state / ('profile-' + key + '-' + kind + '.json')
    path.write_text(json.dumps(manifest, indent=2) + '\n')
    path.chmod(0o600)
    result.update(active=True, env=env, args=args, manifest=str(path))
    return result


def main():
    if len(sys.argv) < 2:
        raise SystemExit('usage: codex-startup.py BINARY [ARG ...] | --plan')
    try:
        plan = prepare(os.environ)
    except (OSError, ValueError) as error:
        print('codex-startup: retaining full context: ' + str(error), file=sys.stderr)
        plan = {'active': False, 'env': dict(os.environ), 'args': []}
    if sys.argv[1] == '--plan':
        print(json.dumps(dict({k: v for k, v in plan.items() if k != 'env'}, codex_home=plan['env'].get('CODEX_HOME')), indent=2))
        return
    os.execvpe(sys.argv[1], [sys.argv[1], *plan['args'], *sys.argv[2:]], plan['env'])


if __name__ == '__main__':
    main()
