#!/usr/bin/env python3
"""Stop explicit Actions starts before Bash in Claude and Codex.

Rides on the already registered gcp-account-guard, like pane-fit/job-host/fix-round,
so open sessions do not need a new hook registration or Codex trust approval.
This prevents accidental starts, not arbitrary code hidden in scripts or aliases.
Release creation and git push deliberately remain outside this guard.
"""
from __future__ import annotations

import os
import re
import shlex

REASON = 'ACTIONS_RUN_REASON'
DENY = (
    'actions-run-guard: GitHub Actions 사용량이 이미 초과되어 확인만을 위한 새 실행을 막습니다. '
    '기존 실행의 로그를 확인하거나 로컬에서 시험하세요. 꼭 필요한 실행은 해당 명령 앞에 '
    "ACTIONS_RUN_REASON='필요한 실행의 구체적인 이유'를 붙이세요(8자 이상인 직접 적은 이유). "
    '예: ACTIONS_RUN_REASON=\'required release recovery\' gh workflow run build.yml. '
    '이 예외는 실행 필요성을 기록하며 배포 승인이나 다른 안전 훅을 대신하지 않습니다.'
)
ASSIGNMENT = re.compile(r'^[A-Za-z_]\w*=')
ENDPOINT = re.compile(
    r'(?:^|/)repos/[^/]+/[^/]+/(?:'
    r'actions/workflows/[^/]+/(?:dispatches|enable)|'
    r'actions/runs/[^/]+/(?:rerun|rerun-failed-jobs)|'
    r'actions/(?:runs/[^/]+/)?jobs/[^/]+/rerun|dispatches)/?$'
)
BOUNDARY = set(';|&()\n')


def reason_ok(value: str) -> bool:
    # An inherited variable or "$REASON" is not a reason recorded on this call.
    return len(value.strip()) >= 8 and not any(c in value for c in '$`')


def starts_actions(args: list[str]) -> bool:
    """Recognize gh CLI operations and equivalent REST writes, never perform them."""
    if '--help' in args or '-h' in args:
        return False
    # gh accepts persistent repository/host options between command words too.
    cleaned = []
    i = 0
    while i < len(args):
        if args[i] in ('-R', '--repo', '--hostname'):
            i += 2
            continue
        if args[i].startswith(('--repo=', '--hostname=', '-R')):
            i += 1
            continue
        cleaned.append(args[i])
        i += 1
    args = cleaned
    if len(args) >= 2 and ((args[0] == 'workflow' and args[1] in ('run', 'enable'))
                           or args[:2] == ['run', 'rerun']):
        return True
    if not args or args[0] != 'api':
        return False
    method = None
    data = False
    endpoint = False
    i = 1
    while i < len(args):
        arg = args[i]
        if arg in ('-X', '--method') and i + 1 < len(args):
            i += 1
            method = args[i].upper()
        elif arg.startswith('--method=') or (arg.startswith('-X') and len(arg) > 2):
            method = arg.split('=', 1)[1].upper() if '=' in arg else arg[2:].upper()
        elif arg in ('-f', '-F', '--field', '--raw-field', '--input'):
            data = True
            i += 1
        elif arg.startswith(('--field=', '--raw-field=', '--input=', '-f', '-F')):
            data = True
        elif arg in ('-H', '--header', '--hostname', '--cache', '-q', '--jq', '-t', '--template'):
            i += 1
        elif ENDPOINT.search(arg.split('?', 1)[0]):
            endpoint = True
        i += 1
    return endpoint and (method or ('POST' if data else 'GET')) in ('POST', 'PUT', 'PATCH')


def command_denied(words: list[str], depth: int) -> bool:
    """Judge a simple command; reason assignments apply only to that command."""
    reason = ''
    i = 0
    while i < len(words):
        word = words[i]
        name = os.path.basename(word)
        if ASSIGNMENT.match(word):
            key, value = word.split('=', 1)
            if key == REASON:
                reason = value
            i += 1
        elif name in ('env', 'command', 'exec', 'sudo', 'nohup', 'time'):
            i += 1
            while i < len(words) and words[i].startswith('-'):
                # env -u/--unset must not turn its operand into the command.
                flag = words[i]
                i += 2 if flag in ('-u', '--unset', '-C', '--chdir') else 1
        elif word in ('then', 'do', 'if', 'elif', 'while', 'until', '!', '{'):
            i += 1
        else:
            break
    if i >= len(words):
        return False
    name = os.path.basename(words[i])
    args = words[i + 1:]
    if name == 'gh':
        return starts_actions(args) and not reason_ok(reason)
    if name in ('bash', 'sh', 'zsh'):
        for n, arg in enumerate(args[:-1]):
            if arg.startswith('-') and not arg.startswith('--') and 'c' in arg[1:]:
                return denied(args[n + 1], depth + 1)
    return False


def substitutions(command: str):
    """Extract common shell substitutions without executing text quoted as data."""
    pattern = re.compile(r'\$\(([^()]*)\)|`([^`]*)`')
    quote = ''
    i = 0
    while i < len(command):
        char = command[i]
        if char == '\\' and quote != "'":
            i += 2
            continue
        if char == quote:
            quote = ''
        elif not quote and char in "'\"":
            quote = char
        elif not quote and char == '#' and (i == 0 or command[i - 1].isspace()):
            end = command.find('\n', i)
            i = len(command) if end < 0 else end
            continue
        elif quote != "'":
            match = pattern.match(command, i)
            if match:
                yield match.group(1) or match.group(2) or ''
                i = match.end()
                continue
        i += 1


def denied(command: str, depth: int = 0) -> bool:
    if depth > 8:
        return True
    # A heredoc sent to cat is data; a heredoc sent to a shell is executable.
    heredoc = re.compile(r"<<-?\s*(['\"]?)(\w+)\1([^\n]*)\n(.*?)\n[\t ]*\2\b", re.S)

    def body(match):
        before = command[:match.start()].rsplit('\n', 1)[-1]
        after = match.group(3)
        shell = r'(?:^|[;&|])\s*(?:\S*/)?(?:bash|sh|zsh)(?:\s+-\S+)*\s*$'
        if re.search(shell, before) or re.search(r'\|\s*(?:bash|sh|zsh)\s*$', after):
            return after + '\n' + match.group(4) + '\n'
        if not match.group(1):
            return after + '\n' + '\n'.join(substitutions(match.group(4))) + '\n'
        return after + '\n'

    command = heredoc.sub(body, command)
    if any(denied(inner, depth + 1) for inner in substitutions(command)):
        return True
    lexer = shlex.shlex(command.replace('\\\n', ''), posix=True, punctuation_chars=';|&()\n')
    lexer.whitespace = ' \t\r'
    lexer.wordchars += '{}'
    words = []
    try:
        for word in lexer:
            if word and set(word) <= BOUNDARY:
                if command_denied(words, depth):
                    return True
                words = []
            else:
                words.append(word)
        return command_denied(words, depth)
    except ValueError:
        return True  # A malformed Actions command must be split or corrected.


def check(payload: dict, env: dict) -> str | None:
    if payload.get('tool_name') != 'Bash':
        return None
    command = str((payload.get('tool_input') or {}).get('command') or '')
    # Keep unrelated commands cheap and do not parse their shell syntax.
    if not re.search(r'\bgh\b', command) or not re.search(r'workflow|rerun|dispatches|/enable', command):
        return None
    return DENY if denied(command) else None
