#!/usr/bin/env python3
"""Idempotently install startup/metering hooks without replacing safety hooks."""
import importlib.util
import json
from pathlib import Path
import shlex

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('handoff_state', HERE / 'handoff-state.py')
state = importlib.util.module_from_spec(spec)
spec.loader.exec_module(state)


def install(path, codex=False):
    data = json.loads(path.read_text()) if path.exists() else {}
    hooks = data.setdefault('hooks', {})
    for event in ['SessionStart', 'Stop']:
        groups = hooks.setdefault(event, [])
        kept = []
        memory_entry = None
        for group in groups:
            entries = []
            for entry in group['hooks']:
                command = entry.get('command', '')
                if 'task-ledger.py hook ' in command:
                    continue
                if codex and event == 'SessionStart' and ('claude-memory.sh' in command or 'codex-memory.py' in command):
                    memory_entry = dict(entry)
                    continue
                entries.append(entry)
            if entries or not group['hooks']:
                kept.append(dict(group, hooks=entries))
        if codex and event == 'SessionStart':
            memory_entry = memory_entry or dict(type='command', timeout=10, additionalContextLimit=60000)
            memory_entry['command'] = 'python3 ' + shlex.quote(str(HERE / 'codex-memory.py'))
            kept.append(dict(hooks=[memory_entry]))
        command = 'python3 ' + shlex.quote(str(HERE / 'task-ledger.py')) + ' hook ' + event
        kept.append(dict(hooks=[dict(type='command', command=command, timeout=10)]))
        hooks[event] = kept
    state.atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    install(Path.home() / '.codex/hooks.json', codex=True)
    install(Path.home() / '.claude/settings.json')
