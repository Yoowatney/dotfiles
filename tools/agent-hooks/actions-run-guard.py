#!/usr/bin/env python3
"""Stop explicit Actions starts before Bash in Claude and Codex.

Rides on the already registered gcp-account-guard, like pane-fit/job-host/fix-round,
so open sessions do not need a new hook registration or Codex trust approval.
This prevents accidental starts, not arbitrary code hidden in scripts or aliases.
Release creation and git push deliberately remain outside this guard.
It also refuses to rerun a pull_request run that is not of the PR's current head, reason or not:
under cancel-in-progress the old run cancels the newer commit's checks.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
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
STALE_DENY = (
    'actions-run-guard: run {run}은 PR #{number}의 옛 커밋({old})을 검사한 것입니다. 지금 head는 {new}입니다. '
    '옛 run을 다시 돌리면 cancel-in-progress 설정이 최신 커밋의 검사를 취소할 수 있습니다. '
    '최신 커밋의 run을 다시 돌리세요(gh pr checks {number}로 찾습니다).'
)
UNVERIFIED_DENY = (
    'actions-run-guard: 다시 돌리려는 Actions run이 PR의 최신 커밋인지 확인하지 못했습니다. '
    'run id를 숫자로 직접 쓰고 -R OWNER/REPO를 붙여 한 번에 하나씩 실행하세요.'
)
LOOKUP_SECONDS = 5  # all lookups of one command; the registered hook is cut at 10 s
RERUN_ENDPOINT = re.compile(
    r'(?:^|/)repos/(?P<repo>[^/]+/[^/]+)/actions/(?:'
    r'runs/(?P<run>[^/]+)/(?:rerun|rerun-failed-jobs|jobs/[^/]+/rerun)|jobs/(?P<job>[^/]+)/rerun)/?$'
)
# (repo, run id, job id) of every rerun the current command would be allowed to start.
RERUNS: list[tuple[str, str, str]] = []
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


def rerun_target(args: list[str]) -> tuple[str, str, str] | None:
    """(repo, run id, job id) of a rerun. repo '' leaves gh to resolve it from the working directory."""
    if '--help' in args or '-h' in args:
        return None
    repo = ''
    for index, arg in enumerate(args):
        if arg in ('-R', '--repo') and index + 1 < len(args):
            repo = args[index + 1]
        elif arg.startswith('--repo='):
            repo = arg.split('=', 1)[1]
        elif arg.startswith('-R') and len(arg) > 2:
            repo = arg[2:].removeprefix('=')
    rest = command_args(args)
    if rest[:2] == ['run', 'rerun']:
        run = job = ''
        for index, arg in enumerate(rest[2:], 2):
            if arg in ('-j', '--job') and index + 1 < len(rest):
                job = rest[index + 1]
            elif arg.startswith('--job='):
                job = arg.split('=', 1)[1]
            elif not run and rest[index - 1] not in ('-j', '--job') and (arg.isdigit() or '$' in arg):
                if arg in ('1', '2') and rest[index + 1:index + 2] and rest[index + 1].startswith('>'):
                    continue  # the file descriptor of 2>&1
                run = arg
        return repo, run, job
    for arg in rest[1:]:
        match = RERUN_ENDPOINT.search(arg.split('?', 1)[0])
        if match:
            named = match['repo']
            return '' if '{' in named else named, match['run'] or '', match['job'] or ''
    return None


def stale_rerun(cwd: str, env: dict) -> str | None:
    """Refusal for the first collected rerun that is stale or cannot be checked."""
    deadline = time.monotonic() + LOOKUP_SECONDS

    def gh(repo: str, *args: str):
        result = subprocess.run(['gh', *args], env=dict(env, GH_REPO=repo) if repo else env, cwd=cwd or None,
                                capture_output=True, text=True, check=True,
                                timeout=max(0.1, deadline - time.monotonic()))
        return json.loads(result.stdout)

    for repo, run, job in RERUNS:
        try:
            if job and not run and job.isdigit():
                run = str(gh(repo, 'api', f'repos/{{owner}}/{{repo}}/actions/jobs/{job}')['run_id'])
            if not run.isdigit():
                return UNVERIFIED_DENY
            info = gh(repo, 'run', 'view', run, '--json', 'event,headBranch,headSha')
            if info['event'] != 'pull_request':
                continue
            prs = gh(repo, 'pr', 'list', '--head', info['headBranch'], '--state', 'open',
                     '--json', 'number,headRefOid')
            if prs and all(pr['headRefOid'] != info['headSha'] for pr in prs):
                return STALE_DENY.format(run=run, number=prs[0]['number'], old=info['headSha'][:8],
                                         new=prs[0]['headRefOid'][:8])
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
            return UNVERIFIED_DENY  # rider() would turn an exception into "allowed"
    return None


def command_denied(words: list[str], depth: int, watch_only: bool = False) -> bool:
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
    if i >= len(words):
        return False
    name = os.path.basename(words[i])
    args = words[i + 1:]
    if name == 'gh':
        refused = watches_run(args) or (not watch_only and starts_actions(args) and not reason_ok(reason))
        if not watch_only and not refused and starts_actions(args) and (target := rerun_target(args)):
            RERUNS.append(target)
        return refused
    if name in ('bash', 'sh', 'zsh'):
        for n, arg in enumerate(args[:-1]):
            if arg.startswith('-') and not arg.startswith('--') and 'c' in arg[1:]:
                return denied(args[n + 1], depth + 1, watch_only)
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


def denied(command: str, depth: int = 0, watch_only: bool = False) -> bool:
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
    if any(denied(inner, depth + 1, watch_only) for inner in substitutions(command)):
        return True
    lexer = shlex.shlex(command.replace('\\\n', ''), posix=True, punctuation_chars=';|&()\n')
    lexer.whitespace = ' \t\r'
    lexer.wordchars += '{}'
    words = []
    try:
        for word in lexer:
            if word and set(word) <= BOUNDARY:
                if command_denied(words, depth, watch_only):
                    return True
                words = []
            else:
                words.append(word)
        return command_denied(words, depth, watch_only)
    except ValueError:
        return True  # A malformed Actions command must be split or corrected.


def check(payload: dict, env: dict) -> str | None:
    if payload.get('tool_name') != 'Bash':
        return None
    command = str((payload.get('tool_input') or {}).get('command') or '')
    # Keep unrelated commands cheap and do not parse their shell syntax.
    if not re.search(r'\bgh\b', command) or not re.search(r'workflow|rerun|dispatches|/enable|watch', command):
        return None
    if 'watch' in command and denied(command, watch_only=True):
        return WATCH_DENY
    RERUNS.clear()
    if denied(command):
        return DENY
    return stale_rerun(str(payload.get('cwd') or ''), env)
