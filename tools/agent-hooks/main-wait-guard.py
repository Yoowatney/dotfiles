#!/usr/bin/env python3
"""PreToolUse hook: stop the main pane from waiting on while messages sent to it sit in pane-tell's queue.

On 2026-11-26 the main pane waited with Poll for about two hours while 47 messages stayed queued behind a delivery
fault. Before a waiting call (Poll, or a Bash/REPL command that sleeps or watches) in the main pane, this looks for
queued messages for that pane older than ten minutes and refuses the call once, naming the files to read.

Waits that have to happen, such as one for an execution approval, need no exception: each queued message refuses at
most one call per session, so repeating the same call goes through. Commands that handle messages (pane-tell,
pane-spawn, main-channel.py, board.sh) are never refused. Any error lets the call through.

A refusal cannot cut short a wait already running, nor one for a script sent in the same reply (Claude Code 2.1.332
waits for that script even when its Poll is refused). It stops the next wait.

    main-wait-guard.py             hook: PreToolUse JSON on stdin
    main-wait-guard.py install     register in ~/.claude/settings.json (uninstall removes it)
"""
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time

AGE = 600
MATCHER = 'Poll|REPL|Bash'
HANDLING = re.compile(r'\bpane-tell\b|\bpane-spawn\b|\bmain-channel\.py\b|\bboard\.sh\b')
# ponytail: reads the command text, so a wait hidden inside another script is not seen. Poll is the main pane's real
# wait and needs no pattern.
WAITING = re.compile(r'--watch\b|\brun\s+watch\b|(?:\bwhile\b|\buntil\b|\bfor\s*\(|\bfor\s+\w+\s+in\b)[\s\S]{0,400}?\bsleep[\s(]')
SHELL_SLEEP = re.compile(r'\bsleep\s+(\d+(?:\.\d+)?)([smh]?)')
JS_SLEEP = re.compile(r'\bsleep\(\s*([\d_]+)')


def is_wait(tool, tool_input):
    if tool == 'Poll':
        return True
    text = tool_input.get({'Bash': 'command', 'REPL': 'code'}.get(tool, ''), '')
    if not text or tool_input.get('run_in_background') or HANDLING.search(text):
        return False
    return bool(WAITING.search(text)
                or any(float(n) * {'m': 60, 'h': 3600}.get(unit, 1) >= 60 for n, unit in SHELL_SLEEP.findall(text))
                or any(int(n.replace('_', '')) >= 60000 for n in JS_SLEEP.findall(text)))


def queued(state, terminal):
    """(mtime, path) of every message pane-tell still holds for this default-server terminal. Contents are not read."""
    found = []
    for path in (state / 'waiting').glob('*'):
        try:
            to, socket = (state / 'receivers' / path.name).read_text().splitlines()[:2]
            if to == terminal and not socket.endswith('/sessions/workers/herdr.sock'):
                found.append((path.stat().st_mtime, str(path)))
        except (OSError, ValueError):
            continue  # delivered between the two reads, or not a queued message
    # Only the held messages: jobs/ and finished/ keep hex-named metadata after delivery.
    for path in [p for held in ('open', 'claimed') for p in (state / 'batch' / terminal / held).glob('[0-9]*')]:
        try:
            found.append((path.stat().st_mtime, str(path)))
        except OSError:
            continue
    return sorted(found)


def check(payload, env):
    """The refusal text, or None to let the call through."""
    if payload.get('hook_event_name') != 'PreToolUse' or payload.get('agent_id'):
        return None
    if not is_wait(payload.get('tool_name'), payload.get('tool_input') or {}):
        return None
    base = Path(env.get('XDG_STATE_HOME') or Path.home() / '.local/state')
    terminal = (base / 'orchestrator/main').read_text().strip()
    state = base / 'pane-tell'
    now = time.time()
    old = [(t, p) for t, p in queued(state, terminal) if now - t > AGE] if terminal else []
    if not old:
        return None
    # Asked last: only a call that would be refused costs a herdr lookup.
    row = subprocess.check_output([env.get('HERDR_BIN_PATH') or 'herdr', 'pane', 'get', env['HERDR_PANE_ID']],
                                  env=env, text=True, timeout=3, stderr=subprocess.DEVNULL)
    if json.loads(row)['result']['pane']['terminal_id'] != terminal:
        return None
    marker = state / 'wait-guard.json'
    try:
        told = json.loads(marker.read_text())
    except FileNotFoundError:
        told = {}
    session = payload.get('session_id')
    paths = [p for _, p in old]
    if told.get('session') == session and set(paths) <= set(told.get('paths', [])):
        return None
    # Written before refusing: if it cannot be kept, the error lets the call through instead of refusing every wait.
    marker.write_text(json.dumps({'session': session, 'paths': paths}))
    more = ' 외 %d건' % (len(paths) - 3) if len(paths) > 3 else ''
    return ('main-wait-guard: 이 칸 앞으로 온 글 %d건이 전달되지 못한 채 가장 오래된 것은 %d분째 기다립니다. '
            '더 기다리기 전에 지금 읽고 처리하세요: %s%s. 파일은 지우지 마세요. '
            '실행 허락 ✅처럼 꼭 필요한 대기는 처리한 뒤 같은 호출을 다시 하면 통과합니다.'
            % (len(paths), (now - old[0][0]) // 60, ', '.join(paths[:3]), more))


def install(remove):
    path = Path.home() / '.claude/settings.json'
    data = json.loads(path.read_text()) if path.exists() else {}
    script = shlex.quote(str(Path(__file__).resolve()))
    # A missing script makes python3 exit 2, which Claude Code reads as "block": a Mac without this file must pass.
    command = '[ ! -f %s ] || python3 -I %s' % (script, script)
    hooks = data.setdefault('hooks', {})
    groups = []
    for group in hooks.get('PreToolUse', []):
        rest = [e for e in group.get('hooks', []) if e.get('command') != command]
        if len(rest) == len(group.get('hooks', [])):
            groups.append(group)
        elif rest:
            groups.append(dict(group, hooks=rest))
    if not remove:
        groups.append({'matcher': MATCHER, 'hooks': [{'type': 'command', 'command': command, 'timeout': 5}]})
    hooks['PreToolUse'] = groups
    new = path.with_name(path.name + '.new')
    new.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    new.chmod(path.stat().st_mode & 0o777 if path.exists() else 0o600)
    os.replace(new, path)


def main():
    if sys.argv[1:] in (['install'], ['uninstall']):
        install(sys.argv[1] == 'uninstall')
        return
    try:
        reason = check(json.load(sys.stdin), os.environ)
    except Exception:  # a broken guard must never stop the main pane
        return
    if reason:
        print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
                                                 'permissionDecisionReason': reason}}, ensure_ascii=False))


if __name__ == '__main__':
    main()
