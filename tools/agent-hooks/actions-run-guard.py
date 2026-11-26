#!/usr/bin/env python3
"""Stop explicit Actions starts before Bash in Claude and Codex.

Rides on the already registered gcp-account-guard, like pane-fit/job-host/fix-round,
so open sessions do not need a new hook registration or Codex trust approval.
This prevents accidental starts, not arbitrary code hidden in scripts or aliases.
Release creation and git push deliberately remain outside this guard.
It also protects the shared REST quota: no sub-minute REST polling loop, and a per-session hourly budget.
A loop written in the calling script (one command per iteration) is only caught by the budget.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import shlex
import time

REASON = 'ACTIONS_RUN_REASON'
WATCH_DENY = (
    'actions-run-guard: CI 감시는 60초 간격과 같은 run의 중복 방지를 적용하는 공용 진입점을 쓰세요: '
    'uv run --no-project python3 ~/.dotfiles/tools/agent-hooks/ci-watch.py --repo OWNER/REPO RUN_ID. '
    '이미 감시 중이면 종료 코드 75를 반환합니다. CI 성공으로 해석하지 마세요.'
    ' PR 검사 감시는 gh pr checks --watch --interval 60처럼 60초 이상을 지정하세요.'
)
DENY = (
    'actions-run-guard: GitHub Actions 사용량이 이미 초과되어 확인만을 위한 새 실행을 막습니다. '
    '기존 실행의 로그를 확인하거나 로컬에서 시험하세요. 꼭 필요한 실행은 해당 명령 앞에 '
    "ACTIONS_RUN_REASON='필요한 실행의 구체적인 이유'를 붙이세요(8자 이상인 직접 적은 이유). "
    '예: ACTIONS_RUN_REASON=\'required release recovery\' gh workflow run build.yml. '
    '이 예외는 실행 필요성을 기록하며 배포 승인이나 다른 안전 훅을 대신하지 않습니다.'
)
# The REST core quota (5,000/h) is shared by every session of one account. Fetching hundreds of job logs
# one call at a time, or polling every few seconds, can spend it and stop everyone else.
REST_REASON = 'GH_REST_REASON'
REST_BUDGET = 300  # per session per hour; ordinary work stays well below it
REST_POLL = 60  # seconds, the same floor as CI watching
ALTERNATIVES = (
    'PR 검사는 gh pr checks --watch --interval 60, Actions run은 '
    'uv run --no-project python3 ~/.dotfiles/tools/agent-hooks/ci-watch.py --repo OWNER/REPO RUN_ID, '
    '여러 PR·issue 조회는 gh pr view --json이나 gh api graphql 한 번으로 묶으세요(GraphQL도 시간당 한도는 따로 있습니다).'
)
LOOP_DENY = (
    f'actions-run-guard: gh REST 호출을 {REST_POLL}초보다 짧은 간격으로 반복하면 모든 세션이 함께 쓰는 '
    '시간당 5,000회 한도가 바닥납니다. ' + ALTERNATIVES + f' 꼭 REST로 반복해야 하면 sleep {REST_POLL} 이상을 쓰세요.'
)
BUDGET_DENY = (
    f'actions-run-guard: 이 세션이 최근 1시간에 gh REST를 {REST_BUDGET}회 넘게 부르려 합니다. 한도(시간당 5,000회)는 '
    '모든 세션이 함께 씁니다. 이미 받은 결과(로그, job 목록)는 파일로 남겨 다시 쓰고, 표본만 받거나 GraphQL로 묶으세요. '
    + ALTERNATIVES + f" 꼭 필요하면 명령 앞에 {REST_REASON}='구체적인 이유'(8자 이상)를 붙이세요."
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


def command_args(args: list[str]) -> list[str]:
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
    return cleaned


def watches_run(args: list[str]) -> bool:
    if any(a in args for a in ('--help', '-h')):
        return False
    args = command_args(args)
    if args[:2] == ['run', 'watch']:
        return True
    if args[:2] != ['pr', 'checks']:
        return False
    watch = False
    interval = '10'
    for index, arg in enumerate(args[2:], 2):
        if arg == '--watch':
            watch = True
        elif arg.startswith('--watch='):
            watch = arg.split('=', 1)[1].lower() not in ('false', 'f', '0')
        elif arg in ('--interval', '-i'):
            interval = args[index + 1] if index + 1 < len(args) else ''
        elif arg.startswith('--interval='):
            interval = arg.split('=', 1)[1]
        elif arg.startswith('-i'):
            interval = arg[2:].removeprefix('=')
    try:
        # pflag uses Go's base-zero ParseInt: leading zero means octal.
        digits = interval.lstrip('+-')
        base = 8 if len(digits) > 1 and digits[0] == '0' and digits[1] not in 'xXoObB' else 0
        return watch and int(interval, base) < 60
    except ValueError:
        return watch


def starts_actions(args: list[str]) -> bool:
    """Recognize gh CLI operations and equivalent REST writes, never perform them."""
    if '--help' in args or '-h' in args:
        return False
    args = command_args(args)
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


def uses_rest(args: list[str]) -> bool:
    """gh subcommands that spend the REST core quota. gh pr/issue use GraphQL; rate_limit is free."""
    if '--help' in args or '-h' in args:
        return False
    args = command_args(args)
    if args[:1] == ['run']:
        return args[1:2] != [] and args[1] in ('list', 'view', 'download', 'rerun', 'cancel')
    if args[:1] == ['workflow']:
        return args[1:2] != [] and args[1] in ('list', 'view', 'run', 'enable')
    if args[:1] != ['api']:
        return False
    i = 1
    while i < len(args):
        if args[i] in ('-X', '--method', '-f', '-F', '--field', '--raw-field', '--input', '-H', '--header',
                       '--cache', '-q', '--jq', '-t', '--template', '-p', '--preview'):
            i += 2
        elif args[i].startswith('-'):
            i += 1
        else:
            return args[i].strip('/') not in ('graphql', 'rate_limit')
    return False


def short_sleep(value: str) -> bool:
    match = re.fullmatch(r'(\d+(?:\.\d+)?)([smhd]?)', value)
    # An interval the hook cannot read (a variable) counts as short.
    return not match or float(match[1]) * {'m': 60, 'h': 3600, 'd': 86400}.get(match[2], 1) < REST_POLL


def command_denied(words: list[str], depth: int, watch_only: bool = False, seen: dict | None = None) -> bool:
    """Judge a simple command; reason assignments apply only to that command.

    With seen, only collect REST calls and polling-loop signs and deny nothing."""
    reason = rest_reason = ''
    i = 0
    while i < len(words):
        word = words[i]
        name = os.path.basename(word)
        if ASSIGNMENT.match(word):
            key, value = word.split('=', 1)
            if key == REASON:
                reason = value
            elif key == REST_REASON:
                rest_reason = value
            i += 1
        elif name in ('timeout', 'gtimeout', 'nice', 'gnice', 'stdbuf', 'gstdbuf'):
            i += 1
            value_flags = {'-k', '--kill-after', '-s', '--signal'} if name.endswith('timeout') else (
                {'-n', '--adjustment'} if name.endswith('nice') else
                {'-i', '--input', '-o', '--output', '-e', '--error'})
            while i < len(words) and words[i].startswith('-'):
                flag = words[i]
                i += 2 if flag in value_flags else 1
                if flag == '--':
                    break
            if name.endswith('timeout'):
                i += 1  # The duration precedes the wrapped command.
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
    if seen is not None and {'while', 'until'} & set(words[:i]):
        seen['loop'] = True
    if i >= len(words):
        return False
    name = os.path.basename(words[i])
    args = words[i + 1:]
    if seen is not None:
        if name == 'for':
            seen['loop'] = True
        elif name == 'sleep' and args:
            seen['short' if short_sleep(args[0]) else 'long'] = True
        elif name == 'watch' and 'gh' in args:
            head = args[:args.index('gh')]
            interval = next((a[2:] or b for a, b in zip(head, head[1:] + ['']) if a.startswith('-n')), '2')
            seen['loop'] = True
            seen['short' if short_sleep(interval.lstrip('=')) else 'long'] = True
            name, args = 'gh', args[args.index('gh') + 1:]
        if name == 'gh':
            if uses_rest(args):
                seen['calls'].append(reason_ok(rest_reason))
            return False
    if name == 'gh':
        return watches_run(args) or (not watch_only and starts_actions(args) and not reason_ok(reason))
    if name in ('bash', 'sh', 'zsh'):
        for n, arg in enumerate(args[:-1]):
            if arg.startswith('-') and not arg.startswith('--') and 'c' in arg[1:]:
                return denied(args[n + 1], depth + 1, watch_only, seen)
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


def denied(command: str, depth: int = 0, watch_only: bool = False, seen: dict | None = None) -> bool:
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
    inners = list(substitutions(command))
    if any([denied(inner, depth + 1, watch_only, seen) for inner in inners]):
        return True
    if seen is not None:
        for inner in inners:  # counted above; the lexer below would see an unquoted one again
            command = command.replace(f'$({inner})', 'SUB').replace(f'`{inner}`', 'SUB')
    lexer = shlex.shlex(command.replace('\\\n', ''), posix=True, punctuation_chars=';|&()\n')
    lexer.whitespace = ' \t\r'
    lexer.wordchars += '{}'
    words = []
    try:
        for word in lexer:
            if word and set(word) <= BOUNDARY:
                if command_denied(words, depth, watch_only, seen):
                    return True
                words = []
            else:
                words.append(word)
        return command_denied(words, depth, watch_only, seen)
    except ValueError:
        return True  # A malformed Actions command must be split or corrected.


def over_budget(session: str, calls: list[bool], env: dict) -> bool:
    """Record this command's REST calls; True refuses it. Calls with a reason are recorded and never refused.

    ponytail: one small file per session and no lock, so parallel calls may overshoot by a few; --paginate and
    loops inside one command count as one call. Machine-wide accounting if one-session limits stop being enough."""
    try:
        directory = Path(env['HOME']) / '.local/state/gh-rest-budget'
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / re.sub(r'[^\w-]', '_', session)
        now = int(time.time())
        text = path.read_text() if path.exists() else ''
        recent = [line for line in text.split() if line.isdigit() and int(line) > now - 3600]
        if not all(calls) and len(recent) + len(calls) > REST_BUDGET:
            return True
        path.write_text(''.join(f'{line}\n' for line in recent + [str(now)] * len(calls)))
    except (OSError, KeyError):
        pass  # accounting trouble never blocks work
    return False


def rest_problem(command: str, session: str, env: dict) -> str | None:
    seen = {'calls': [], 'loop': False, 'short': False, 'long': False}
    denied(command, seen=seen)
    if not seen['calls']:
        return None
    # A slow poll may still pause briefly before exiting, so one long enough sleep passes the command.
    if seen['loop'] and seen['short'] and not seen['long']:
        return LOOP_DENY
    if session and over_budget(session, seen['calls'], env):
        return BUDGET_DENY
    return None


def check(payload: dict, env: dict) -> str | None:
    if payload.get('tool_name') != 'Bash':
        return None
    command = str((payload.get('tool_input') or {}).get('command') or '')
    # Keep unrelated commands cheap and do not parse their shell syntax.
    if not re.search(r'\bgh\b', command):
        return None
    if re.search(r'workflow|rerun|dispatches|/enable|watch', command):
        if 'watch' in command and denied(command, watch_only=True):
            return WATCH_DENY
        if denied(command):
            return DENY
    if not re.search(r'\b(?:api|run|workflow)\b', command):
        return None
    return rest_problem(command, str(payload.get('session_id') or ''), env)
