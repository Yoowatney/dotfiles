#!/usr/bin/env python3
"""Shared Claude memory and ancestor rules, with a conservative worker view."""
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

spec = importlib.util.spec_from_file_location('codex_startup', Path(__file__).with_name('codex-startup.py'))
startup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(startup)


def context(cwd, env):
    home, kind, compact = startup.policy(env)
    cwd = Path(cwd).absolute()
    ancestors = [cwd, *cwd.parents]
    root = next((p for p in ancestors if (p / '.git').exists()), cwd)
    seen = {(p / 'AGENTS.md').resolve() for p in ancestors[:ancestors.index(root) + 1] if (p / 'AGENTS.md').is_file()}
    def rules(paths):
        chunks = []
        for path in paths:
            if not path.is_file() or path.resolve() in seen:
                continue
            seen.add(path.resolve())
            text = path.read_text()
            if compact:
                try:
                    text = startup.worker_rules(text)
                except ValueError:
                    pass  # Never omit rules when boundaries are malformed.
            chunks.append('## ' + str(path) + '\n' + text)
        return '\n\n'.join(chunks)
    parents = rules([p / 'AGENTS.md' for p in reversed(root.parents) if p != Path('/')])
    locals_ = rules([p / 'CLAUDE.local.md' for p in reversed(ancestors) if p != Path('/')])
    def memory_path(path):
        return home / '.claude/projects' / re.sub('[^A-Za-z0-9]', '-', str(path)) / 'memory'
    indexes, files = [], []
    for parent in ancestors:
        if parent == Path('/'):
            continue
        directory = memory_path(parent)
        path = directory / 'MEMORY.md'
        if path.is_file() and path.resolve() not in seen:
            seen.add(path.resolve())
            files.append(str(path))
            text = ''.join(path.read_text().splitlines(keepends=True)[:200])
            indexes.append('## Memory directory: ' + str(directory) + '\n' + startup.memory_index(text, kind if compact else 'full'))
    write = str(Path(files[0]).parent if files else memory_path(cwd))
    parts = ['# Shared memory (Claude + Codex)', 'Write new memories to: ' + write,
             'Index files (read the full file when relevant; omitted domain entries remain there):',
             '\n'.join('- ' + p for p in files) or '(no memories yet for this project)']
    if parents:
        parts += ['# Parent AGENTS.md (above this git root, not auto-loaded by Codex)', parents]
    if locals_:
        parts += ['# CLAUDE.local.md (personal project rules)', locals_]
    parts += ['# Memory indexes', '\n\n'.join(indexes)]
    return '\n\n'.join(parts)


if __name__ == '__main__':
    try:
        payload = json.load(sys.stdin)
        text = context(payload.get('cwd') or os.getcwd(), os.environ)
        print(json.dumps({'hookSpecificOutput': {'hookEventName': 'SessionStart', 'additionalContext': text}}))
    except (OSError, ValueError) as error:
        print('codex-memory: ' + str(error), file=sys.stderr)
        sys.exit(1)
