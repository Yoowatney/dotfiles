#!/usr/bin/env python3
"""Stop explicit Actions starts before Bash in Claude and Codex.

Rides on the already registered gcp-account-guard, like pane-fit/job-host/fix-round,
so open sessions do not need a new hook registration or Codex trust approval.
This prevents accidental starts, not arbitrary code hidden in scripts or aliases.
Release creation and git push deliberately remain outside this guard.
It also protects the shared REST quota: no sub-minute REST polling loop, and a per-session hourly budget.
A loop written in the calling script (one command per iteration) is only caught by the budget.
Unfiltered project exports above one page also need a recorded reason: --jq filters only after fetching.
It also refuses to rerun a pull_request run that is not of the PR's current head, reason or not:
under cancel-in-progress the old run cancels the newer commit's checks.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
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
# The REST core quota (5,000/h) is shared by every session of one account. Fetching hundreds of job logs
# one call at a time, or polling every few seconds, can spend it and stop everyone else.
REST_REASON = 'GH_REST_REASON'
GRAPHQL_REASON = 'GH_GRAPHQL_REASON'
PROJECT_DENY = (
    'actions-run-guard: unfiltered gh project item-list above 100 items fetches multiple GraphQL pages; '
    '--jq filters only after fetching them. Reuse a saved result, use --limit 100 or less, '
    'use a nonempty --query server filter, or read a specific issue with gh issue view --json projectItems. '
    f"For a necessary full export, prefix this command with {GRAPHQL_REASON}='specific reason for this export' "
    '(at least 8 literal characters). This exception does not bypass REST or Actions guards.'
)
REST_BUDGET = 300  # per session per hour; ordinary work stays well below it
REST_POLL = 60  # seconds, the same floor as CI watching
POLLING = {'call', 'short'}  # a loop holding exactly these (no long sleep) polls too fast
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
# A comment outside quotes; shlex would also swallow its newline and glue the next command to this one.
# Backticks inside a double-quoted string are read as one piece, so quotes nested in them do not end the string.
QUOTED = r"""'[^']*'|"(?:`(?:\\.|[^`\\])*`|\\.|[^"\\`])*\""""
COMMENT_START = r'(?<![^\s;&|()])#[^\n]*'
COMMENT = re.compile(rf'({QUOTED}|\\.)|{COMMENT_START}', re.S)
# A backslash-newline outside quotes and comments, which the shell removes before it looks for comments.
CONTINUED = re.compile(rf'({QUOTED}|{COMMENT_START}|\\[^\n])|\\\n', re.S)


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


def broad_project_read(args: list[str]) -> bool:
    """An explicit export beyond one page without a known, nonempty server filter."""
    args = command_args(args)
    if args[:2] != ['project', 'item-list']:
        return False
    limit = '30'  # gh's default
    query = ''
    i = 2
    while i < len(args):
        arg = args[i]
        if arg in ('--help', '-h'):
            return False
        if arg in ('--limit', '-L', '--query'):
            value = args[i + 1] if i + 1 < len(args) else ''
            if arg == '--query':
                query = value
            else:
                limit = value
            i += 2
            continue
        if arg.startswith('--limit='):
            limit = arg.split('=', 1)[1]
        elif arg.startswith('-L'):
            limit = arg[2:].removeprefix('=')
        elif arg.startswith('--query='):
            query = arg.split('=', 1)[1]
        elif arg in ('--owner', '--format', '--jq', '-q', '--template', '-t'):
            i += 2  # A formatting argument named --query is data, not a server filter.
            continue
        i += 1
    if query.strip() and not any(c in query for c in '$`'):
        return False
    try:
        digits = limit.lstrip('+-')
        base = 8 if len(digits) > 1 and digits[0] == '0' and digits[1] not in 'xXoObB' else 0
        return int(limit, base) > 100
    except ValueError:
        return True  # The hook cannot bound a limit supplied by expansion.


def short_sleep(value: str) -> bool:
    match = re.fullmatch(r'(\d+(?:\.\d+)?)([smhd]?)', value)
    # An interval the hook cannot read (a variable) counts as short.
    return not match or float(match[1]) * {'m': 60, 'h': 3600, 'd': 86400}.get(match[2], 1) < REST_POLL


def command_denied(words: list[str], depth: int, watch_only: bool = False, seen: dict | None = None) -> bool:
    """Judge a simple command; reason assignments apply only to that command.

    With seen, collect REST calls, polling loops and broad project reads and deny nothing.
    seen['blocks'] holds the open loops (what each one contains) and open cases (None)."""
    reason = rest_reason = graphql_reason = ''
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
            elif key == GRAPHQL_REASON:
                graphql_reason = value
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
        elif word in ('then', 'do', 'if', 'elif', 'else', 'while', 'until', '!', '{'):
            i += 1
        else:
            break
    if seen is not None:
        if {'while', 'until'} & set(words[:i]):
            seen['blocks'].append(set())  # before its condition, which runs every round
        # A substitution is judged where it stands, so a loop owns only what it contains.
        # A `for` list is read once, so it is judged before that loop opens.
        for number in re.findall(r'__SUB(\d+)__', ' '.join(words)):
            if int(number) < len(seen['subs']):
                # Judged once, even when a `bash -c "..."` string carries the placeholder again.
                inner, seen['subs'][int(number)] = seen['subs'][int(number)], ''
                denied(inner, depth + 1, watch_only, seen)
    if i >= len(words):
        return False
    name = os.path.basename(words[i])
    args = words[i + 1:]
    if seen is not None:
        blocks = seen['blocks']
        loops = [block for block in blocks if block is not None]
        if name == 'for':
            blocks.append(set())
        elif name == 'case':
            blocks.append(None)
        elif name == 'esac' and blocks and blocks[-1] is None:
            blocks.pop()
        elif name == 'done' and blocks and blocks[-1] is not None:  # inside a case, `done)` is a pattern
            seen['poll'] |= blocks.pop() == POLLING
        elif name == 'sleep' and args:
            for loop in loops:
                loop.add('short' if short_sleep(args[0]) else 'long')
        elif name == 'watch' and 'gh' in args:
            head = args[:args.index('gh')]
            interval = next((a[2:] or b for a, b in zip(head, head[1:] + ['']) if a.startswith('-n')), '2')
            name, args = 'gh', args[args.index('gh') + 1:]
            seen['poll'] |= uses_rest(args) and short_sleep(interval.lstrip('='))
        if name == 'gh':
            seen['project'] |= broad_project_read(args) and not reason_ok(graphql_reason)
            if uses_rest(args):
                seen['calls'].append(reason_ok(rest_reason))
                for loop in loops:
                    loop.add('call')
            return False
    if name == 'gh':
        refused = watches_run(args) or (not watch_only and starts_actions(args) and not reason_ok(reason))
        if not watch_only and not refused and starts_actions(args) and (target := rerun_target(args)):
            RERUNS.append(target)
        return refused
    if name in ('bash', 'sh', 'zsh'):
        for n, arg in enumerate(args[:-1]):
            if arg.startswith('-') and not arg.startswith('--') and 'c' in arg[1:]:
                return denied(args[n + 1], depth + 1, watch_only, seen)
    return False


def substitutions(command: str):
    """Match common shell substitutions without executing text quoted as data."""
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
                yield match
                i = match.end()
                continue
        i += 1


def join_and_strip(command: str) -> str:
    """Join continued lines, then remove the real comments."""
    command = CONTINUED.sub(lambda m: m[1] or '', command)
    return COMMENT.sub(lambda m: m[1] or '', command)


def strip_backtick_comments(command: str) -> str:
    """A comment inside backticks ends at the closing backtick, so it is stripped there before COMMENT runs."""
    for m in reversed(list(substitutions(command))):
        if m[2] is not None:
            inner = f'`{join_and_strip(m[2])}`'
        else:
            inner = f'$({strip_backtick_comments(m[1])})'  # m[1] holds no parentheses, only backticks
        command = f'{command[:m.start()]}{inner}{command[m.end():]}'
    return command


def uncommented(command: str) -> str:
    """The command as the shell reads it for comments: backtick contents first, then the whole line."""
    return join_and_strip(strip_backtick_comments(command))


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
            return after + '\n' + '\n'.join(m[1] or m[2] or '' for m in substitutions(match.group(4))) + '\n'
        return after + '\n'

    command = heredoc.sub(body, command)
    command = uncommented(command)
    found = list(substitutions(command))
    if seen is None:
        if any([denied(m[1] or m[2] or '', depth + 1, watch_only) for m in found]):
            return True
    else:  # leave a placeholder; command_denied judges the inner command when it reaches it
        for number, m in reversed(list(enumerate(found, len(seen['subs'])))):
            command = f'{command[:m.start()]}__SUB{number}__{command[m.end():]}'
        seen['subs'] += [m[1] or m[2] or '' for m in found]
    lexer = shlex.shlex(command.replace('\\\n', ''), posix=True, punctuation_chars=';|&()\n')
    lexer.commenters = ''  # COMMENT removed the real ones; shlex would also cut at ${#a} or a#b
    lexer.whitespace = ' \t\r'
    lexer.wordchars += '{}#$'
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
    seen = {'calls': [], 'subs': [], 'blocks': [], 'poll': False, 'project': False}
    denied(command, seen=seen)
    if seen['project']:
        return PROJECT_DENY
    if not seen['calls']:
        return None
    # An unclosed loop runs to the end of the command. A slow poll may still pause briefly before
    # exiting, so one long enough sleep in the same loop passes it.
    if seen['poll'] or POLLING in seen['blocks']:
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
    RERUNS.clear()
    if re.search(r'workflow|rerun|dispatches|/enable|watch', command):
        if 'watch' in command and denied(command, watch_only=True):
            return WATCH_DENY
        if denied(command):
            return DENY
        if stale := stale_rerun(str(payload.get('cwd') or ''), env):
            return stale
    if not re.search(r'\b(?:api|run|workflow|project)\b', command):
        return None
    return rest_problem(command, str(payload.get('session_id') or ''), env)
