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
        memory_found = ledger_found = False
        ledger_command = 'python3 ' + shlex.quote(str(HERE / 'task-ledger.py')) + ' hook ' + event
        memory_command = 'python3 ' + shlex.quote(str(HERE / 'codex-memory.py'))
        for group in groups:
            for entry in group['hooks']:
                command = entry.get('command', '')
                if 'task-ledger.py hook ' in command:
                    entry['command'] = ledger_command
                    ledger_found = True
                if codex and event == 'SessionStart' and ('claude-memory.sh' in command or 'codex-memory.py' in command):
                    entry['command'] = memory_command
                    memory_found = True
        # Trust IDs contain group/entry indices. Never reorder existing hooks.
        if codex and event == 'SessionStart' and not memory_found:
            groups.append(dict(hooks=[dict(type='command', timeout=10, additionalContextLimit=60000, command=memory_command)]))
        if not ledger_found:
            groups.append(dict(hooks=[dict(type='command', command=ledger_command, timeout=10)]))
    if not codex:
        groups = hooks.setdefault('PostToolUse', [])
        command = 'python3 ' + shlex.quote(str(HERE / 'post-merge-pull.py'))
        if not any(entry.get('command') == command
                   for group in groups for entry in group.get('hooks', [])):
            groups.append(dict(matcher='Bash', hooks=[dict(
                type='command', command=command, timeout=60)]))
    state.atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    install(Path.home() / '.codex/hooks.json', codex=True)
    install(Path.home() / '.claude/settings.json')
