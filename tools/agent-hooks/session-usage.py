#!/usr/bin/env python3
"""Local character/command estimates, plus a five-minute GitHub quota sample.

Not token or HTTP accounting. Only counts, safe tool names, local source
identifiers and short command labels (known program plus subcommand, or the script's
own description) are persisted. Transcript bodies and command arguments never are.
"""
import collections
import datetime
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
INTERVAL = 300
MAX_BYTES = 32 * 1024 * 1024
SOURCE_BYTES = 2 * 1024 * 1024
SUBCOMMAND_TOOLS = ('bq', 'docker', 'firebase', 'gcloud', 'gh', 'git', 'herdr', 'kubectl', 'npm', 'terraform', 'uv')
# The only program names ever stored. The shell reading below is approximate and can put an
# argument (maybe a secret) where a command starts; an unlisted word is dropped.
PROGRAMS = frozenset(SUBCOMMAND_TOOLS + tuple('''
awk bash brew cargo cat chmod cp curl cut date diff du env find go graphify grep head jest jq kill
launchctl ln ls lsof make mkdir mv node npx open pane-label pane-spawn pane-tell perl pgrep pkill
pnpm ps pytest python python3 rg rm rsync ruby scp security sed sh sleep sort sqlite3 ssh stat stripe
swift tail tar tee timeout touch tr tsc uniq wc which xargs xcodebuild yarn zsh
'''.split()))


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def state_dir(env):
    return Path(env.get('XDG_STATE_HOME') or str(Path(env.get('HOME', str(Path.home()))) / '.local/state')) / 'orchestrator'


def read_json(path):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.usage-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def command_words(command):
    """Words of each shell command, assignment/env prefixes removed; [] if unreadable."""
    # Backslash-newline continues the same command (an even run of backslashes does not).
    command = re.sub(r'(?<!\\)((?:\\\\)*)\\\r?\n', r'\1', command)
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=';&|()\n')
        lexer.whitespace = ' \t\r'
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return []
    start, found, depth, previous = True, [], 0, ''
    for token in tokens:
        before, previous = previous, token
        separator = token and all(c in ';&|()\n' for c in token)
        if depth or (separator and token.startswith('(') and re.fullmatch(r'[A-Za-z_]\w*\+?=', before)):
            # Inside NAME=( ... ) every line is a value, not a command.
            if separator:
                depth = max(0, depth + token.count('(') - token.count(')'))
                start = not depth
        elif separator:
            start = True
        elif start:
            if re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', token) or token in ('env', 'command', 'exec'):
                continue
            found.append([token])
            start = False
        else:
            found[-1].append(token)
    return found


def gh_count(command):
    """Conservative shell estimate; quoted script bodies are not executed here."""
    if not isinstance(command, str) or '<<' in command:
        return 0
    return sum(Path(words[0]).name == 'gh' for words in command_words(command))


def command_names(commands):
    """Up to three distinct programs from PROGRAMS, with a subcommand for multi-command tools."""
    names = []
    for command in commands:
        for words in command_words(command.split('<<')[0]):
            while words[1:] and words[0] in ('do', 'else', 'if', 'then', 'until', 'while'):
                words = words[1:]
            name = Path(words[0]).name
            if name not in PROGRAMS:
                continue
            rest = words[3:] if words[1:2] == ['-C'] else words[1:]
            if name in SUBCOMMAND_TOOLS and rest and re.fullmatch(r'[a-z][a-z-]{0,20}', rest[0]):
                name += ' ' + rest[0]
            if name not in names:
                names.append(name)
    return ', '.join(names[:3])


def command_label(name, data, wrapped):
    """Name what ran inside a tool call. `wrapped` maps a running script's number to its label."""
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            pass  # a Codex exec script is source text, not JSON
    detail = ''
    if isinstance(data, dict):
        if name == 'wait' and str(data.get('cell_id')) in wrapped:
            return wrapped[str(data['cell_id'])]
        command = data.get('command', data.get('cmd'))
        if script_tool(name) and isinstance(data.get('description'), str):
            detail = ' '.join(data['description'].split())
            detail = re.sub(r'\S+@\S+|[A-Za-z0-9_+/=-]{24,}', '…', detail)
            detail = ''.join(c for c in detail if c.isprintable())[:60]
        if not detail and isinstance(command, str):
            detail = command_names([command])
        data = data.get('code')
    if not detail and script_tool(name) and isinstance(data, str):
        # Claude REPL: $`...`; Codex exec: {cmd: "..."}
        detail = command_names(a or c for a, _, c in re.findall(
            r'\$`([^`]*)`|\bcmd\s*:\s*(["\'`])((?:\\.|(?!\2)[^\\])*)\2', data))
    return f'{name}: {detail}' if detail else name


def split_poll(text, wrapped):
    """Claude's Poll delivers many scripts' output; yield (label, characters) for each.

    Headers, status lines and empty waits ran no command and are not yielded.
    """
    header = re.match(r'<system>authentic event nonces for this delivery: ([^—<]+)—', text)
    nonces = set(re.findall(r'[\w-]+', header[1])) if header else set()
    # Only the delivery's own nonces open an event; anything else is quoted output.
    events = [m for m in re.finditer(r'<event nonce="([\w-]+)"([^>]*)>', text) if m[1] in nonces]
    for index, event in enumerate(events):
        number = re.search(r'\beval="(\d+)"', event[2])
        end = events[index + 1].start() if index + 1 < len(events) else len(text)
        if number:  # an unknown number means its dispatch fell outside the scanned bytes
            yield wrapped.get(number[1], 'Poll'), end - event.start()


def visible_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ''.join(visible_text(x) for x in value)
    if isinstance(value, dict):
        return ''.join(visible_text(value.get(k)) for k in ('text', 'content', 'output'))
    return ''


def text_size(value):
    return len(visible_text(value))


def tool_name(value):
    return value if isinstance(value, str) and re.fullmatch(r'[\w.:/-]{1,100}', value) else 'unknown'


def script_tool(name):
    """Do not confuse opaque script invocations with observed gh commands."""
    return name.lower() == 'repl' or name == 'exec' or name.endswith(
        ('.exec', '__exec', '.REPL', '__REPL', 'repl__js', 'repl.js'))


def timestamp(row):
    try:
        return datetime.datetime.fromisoformat(row['timestamp'].replace('Z', '+00:00')).timestamp()
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def summarize(path, since=None, max_bytes=MAX_BYTES):
    chars, calls, seen = collections.Counter(), {}, set()
    commands, labels, wrapped = collections.Counter(), {}, {}

    def count_output(ident, name, value):
        text = visible_text(value)
        chars['tool_output:' + name] += len(text)
        label = labels.get(ident, name)
        if name == 'Poll':
            for part, size in split_poll(text, wrapped):
                commands[part] += size
            return
        commands[label] += len(text)
        # The number a later Poll (Claude) or wait (Codex) will refer to.
        running = re.search(r'^\(dispatched #(\d+)\)|Script running with cell ID (\d+)', text[:300])
        if running and (script_tool(name) or name == 'wait'):
            wrapped[running[1] or running[2]] = label

    gh, unparsed, malformed, partial = 0, 0, 0, False
    with Path(path).open('rb') as stream:
        size = os.fstat(stream.fileno()).st_size
        if size > max_bytes:
            partial = True
            stream.seek(size - max_bytes)
            stream.readline()  # cut record cannot be counted reliably
        for raw in stream:
            try:
                row = json.loads(raw)
                if not isinstance(row, dict):
                    raise ValueError()
            except (ValueError, UnicodeError):
                malformed += 1
                continue
            identity = row.get('uuid') if isinstance(row.get('uuid'), str) else None
            if identity and identity in seen:
                continue
            if identity:
                seen.add(identity)
            when = timestamp(row)
            recent = since is None or (when is not None and when >= since)
            message = row.get('message')
            if isinstance(message, dict):
                role = message.get('role', row.get('type', 'unknown'))
                role = role if role in ('user', 'assistant', 'system') else 'other'
                content = message.get('content', [])
                if isinstance(content, str):
                    chars[role] += len(content)
                    continue
                for block in content if isinstance(content, list) else []:
                    if not isinstance(block, dict):
                        continue
                    kind = block.get('type')
                    if kind == 'tool_use':
                        name = tool_name(block.get('name'))
                        data = block.get('input', {})
                        if isinstance(block.get('id'), str):
                            calls[block['id']] = name
                            labels[block['id']] = command_label(name, data, wrapped)
                        chars['tool_input:' + name] += len(json.dumps(data, ensure_ascii=False))
                        if recent and name in ('Bash', 'exec_command') and isinstance(data, dict):
                            gh += gh_count(data.get('command', data.get('cmd')))
                        elif recent and script_tool(name):
                            unparsed += 1
                    elif kind == 'tool_result':
                        ident = block.get('tool_use_id')
                        name = calls.get(ident, 'unknown') if isinstance(ident, str) else 'unknown'
                        count_output(ident if isinstance(ident, str) else None, name, block.get('content'))
                    else:
                        chars[role] += text_size(block)
            elif row.get('type') == 'response_item' and isinstance(row.get('payload'), dict):
                p = row['payload']
                kind = p.get('type')
                if kind == 'message':
                    role = p.get('role') if p.get('role') in ('user', 'assistant', 'system', 'developer') else 'other'
                    chars[role] += text_size(p.get('content'))
                elif kind in ('function_call', 'custom_tool_call'):
                    name = tool_name(p.get('name'))
                    data = p.get('arguments', p.get('input', ''))
                    if isinstance(p.get('call_id'), str):
                        calls[p['call_id']] = name
                        labels[p['call_id']] = command_label(name, data, wrapped)
                    chars['tool_input:' + name] += text_size(data)
                    if recent and name.endswith(('exec_command', 'Bash')):
                        try:
                            args = json.loads(data) if isinstance(data, str) else data
                            gh += gh_count(args.get('cmd', args.get('command')))
                        except (ValueError, AttributeError):
                            pass
                    elif recent and script_tool(name):
                        unparsed += 1
                elif kind in ('function_call_output', 'custom_tool_call_output'):
                    ident = p.get('call_id')
                    name = calls.get(ident, 'unknown') if isinstance(ident, str) else 'unknown'
                    count_output(ident if isinstance(ident, str) else None, name, p.get('output'))
    return dict(characters=dict(chars), ranked=[list(x) for x in chars.most_common()],
                top_commands=[list(x) for x in commands.most_common(3) if x[1]],
                gh_commands=gh, unparsed_script_calls=unparsed, malformed_lines=malformed, partial=partial,
                measure='visible_characters_and_gh_commands', hidden_context='not_observed')


def session_start(payload, env, previous=None):
    """Call only after the runtime has established an interactive session."""
    peers = load_module('usage_peers', 'herdr_peers.py')
    main = peers.main_terminal(env)
    pane = peers.herdr(env, 'pane', 'get', env.get('HERDR_PANE_ID', '')).get('pane', {}) if main else {}
    if not main or pane.get('terminal_id') != main:
        return ''
    sid, path = payload.get('session_id'), payload.get('transcript_path')
    # Claude sends SessionStart before creating the new transcript. Only the
    # previous transcript needs to exist for this transition to be counted.
    if not isinstance(sid, str) or not re.fullmatch(r'[\w-]{1,128}', sid) or not isinstance(path, str) or not path:
        return ''
    folder = state_dir(env)
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / 'main-context.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        old = read_json(folder / 'main-context-current.json') or previous or {}
        if old.get('session_id') == sid or old.get('transcript_path') == path:
            return ''
        note = ''
        old_path = old.get('transcript_path')
        if old_path and Path(old_path).is_file():
            result = summarize(old_path)
            result.update(previous_session=old.get('session_id'), next_session=sid, recorded_at=time.time())
            if result['ranked']:
                biggest, count = result['ranked'][0]
                action = ('이 도구의 반복 조회부터 줄이고 필요한 결과만 요약해서 읽으세요.'
                          if biggest.startswith('tool_') else '이 출처의 중복 인용부터 줄이고 필요한 부분만 읽으세요.')
                top = ''
                if result['top_commands']:
                    command, size = result['top_commands'][0]
                    top = f'결과를 가장 많이 낸 명령은 「{command}」 {size:,}자입니다. '
                    if biggest.startswith('tool_output:'):
                        action = '이 명령의 반복 조회부터 줄이고 필요한 결과만 요약해서 읽으세요.'
                note = (f'직전 대화의 보이는 글자 수: 가장 큰 항목 {biggest} {count:,}자. {top}'
                        f'{action} 원문·안전 지침은 보존하세요. 숨은 입력은 미관측이며 토큰 수가 아닙니다.'
                        + (' 일부 기록만 집계했습니다.' if result['partial'] else ''))
            with (folder / 'main-context-history.jsonl').open('a') as stream:
                stream.write(json.dumps(result, ensure_ascii=False) + '\n')
            write_json(folder / 'main-context-latest.json', result)
        write_json(folder / 'main-context-current.json', dict(session_id=sid, transcript_path=path))
        return note


def attribution(sources, since, *, diagnostics=None, max_bytes=MAX_BYTES):
    sources = list(sources)
    result = []
    detail = dict(candidate_files=len(sources), scanned_files=0, partial_files=0,
                  skipped_files=0, unparsed_script_calls=0, byte_budget=max_bytes)
    remaining = max_bytes
    for label, path in sources:
        if remaining <= 0:
            detail['skipped_files'] += 1
            continue
        try:
            limit = min(remaining, SOURCE_BYTES)
            remaining -= min(Path(path).stat().st_size, limit)
            summary = summarize(path, since=since, max_bytes=limit)
            detail['scanned_files'] += 1
            detail['partial_files'] += summary['partial']
            count = summary['gh_commands']
            unparsed = summary['unparsed_script_calls']
            detail['unparsed_script_calls'] += unparsed
            if count or unparsed:
                item = dict(source=label, gh_commands=count)
                if unparsed:
                    item['unparsed_script_calls'] = unparsed
                result.append(item)
        except (OSError, ValueError):
            detail['skipped_files'] += 1
            continue
    if diagnostics is not None:
        diagnostics.update(detail)
    return sorted(result, key=lambda x: (x['gh_commands'], x.get('unparsed_script_calls', 0)), reverse=True)[:10]


def find_sources(env, where, now):
    home = Path(env.get('HOME', str(Path.home())))
    active = {}
    for address, row in (where or {}).values():
        session = row.get('agent_session') or {}
        if isinstance(session, dict) and session.get('value'):
            active[session['value']] = address
    paths = list((home / '.claude/projects').glob('*/*.jsonl'))
    paths += list((Path(env.get('CODEX_HOME', str(home / '.codex'))) / 'sessions').glob('**/*.jsonl'))
    candidates = []
    for path in paths:
        try:
            modified = path.stat().st_mtime
            if modified >= now - 3600:
                candidates.append((modified, path))
        except OSError:
            pass
    sources = []
    for _, path in sorted(candidates, reverse=True)[:128]:
        label = next((address for sid, address in active.items() if sid in path.name), None)
        sources.append((label or 'unmatched-session-' + hashlib.sha256(str(path).encode()).hexdigest()[:8], path))
    return sources


def validate_quota(value):
    resources = value.get('resources') if isinstance(value, dict) else None
    if not isinstance(resources, dict):
        raise ValueError('missing resources')
    result = {}
    for name in ('core', 'graphql'):
        item = resources.get(name)
        if not isinstance(item, dict) or any(type(item.get(k)) is not int for k in ('limit', 'remaining', 'reset')):
            raise ValueError('invalid resource')
        limit, remaining, reset = (item[k] for k in ('limit', 'remaining', 'reset'))
        if limit < 0 or remaining < 0 or remaining > limit or reset <= 0:
            raise ValueError('invalid values')
        result[name] = dict(limit=limit, remaining=remaining, reset=reset)
    return result


def actual_graphql(env, now):
    """Read bounded response metadata, retaining separate live reset windows."""
    root = Path(env.get('XDG_STATE_HOME') or str(Path(env.get('HOME', str(Path.home()))) / '.local/state'))
    path = Path(env.get('GH_GRAPHQL_RESPONSE_LOG', str(root / 'github/graphql-responses.jsonl')))
    try:
        with path.open('rb') as stream:
            start = max(0, stream.seek(0, os.SEEK_END) - 1024 * 1024)
            stream.seek(start)
            lines = stream.read(1024 * 1024).splitlines()
        if start:
            lines = lines[1:]
    except OSError:
        return None
    windows = {}
    for line in lines:
        try:
            row = json.loads(line)
            at = datetime.datetime.fromisoformat(row['at'].replace('Z', '+00:00')).timestamp()
            if not now - 3600 <= at <= now + 60:
                continue
            header = row.get('header') or {}
            if header.get('resource') == 'graphql':
                remaining, limit, reset_at = (header.get(k) for k in ('remaining', 'limit', 'resetAt'))
            else:
                remaining, limit, reset_at = (row.get(k) for k in ('remaining', 'limit', 'resetAt'))
            reset = int(datetime.datetime.fromisoformat(reset_at.replace('Z', '+00:00')).timestamp())
            if (type(remaining) is not int or type(limit) is not int or
                    not 0 <= remaining <= limit or limit <= 0 or reset <= now):
                continue
            if reset not in windows or at >= windows[reset]['observed_at']:
                windows[reset] = dict(limit=limit, remaining=remaining, reset=reset, observed_at=at)
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            continue
    return min(windows.values(), key=lambda q: q['remaining'] / q['limit'], default=None)


def quota_tick(env, now, where=None, *, fetch=None, send=None, sources=None):
    folder = state_dir(env)
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / 'github-quota.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        state = read_json(folder / 'github-quota.json')
        if type(state.get('attempt_at')) not in (int, float):
            state.pop('attempt_at', None)
        if not isinstance(state.get('notified'), list):
            state['notified'] = []
        pending = state.get('pending')
        if pending is not None and (not isinstance(pending, dict) or
                type(pending.get('expires')) not in (int, float) or
                not isinstance(pending.get('message'), str) or not isinstance(pending.get('keys'), list)):
            state.pop('pending', None)
        if send is None:
            peers = load_module('quota_peers', 'herdr_peers.py')
            def send(message):
                destinations = where if where is not None else peers.all_panes(env)
                main = (destinations or {}).get(peers.main_terminal(env))
                if not main:
                    return False
                return subprocess.run([str(HERE / 'pane-tell'), main[0], message], env=env,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10).returncode == 0
        def deliver():
            pending = state.get('pending')
            if pending and pending['expires'] > now:
                try:
                    if send(pending['message']):
                        state['notified'] = pending['keys']
                        state.pop('pending', None)
                except Exception:
                    pass
            elif pending:
                state.pop('pending', None)
        if 0 <= now - state.get('attempt_at', -INTERVAL) < INTERVAL:
            deliver()
            write_json(folder / 'github-quota.json', state)
            return
        state['attempt_at'] = now
        write_json(folder / 'github-quota.json', state)  # failures consume the interval too
        try:
            observed = actual_graphql(env, now)
            quotas = {}
            state.pop('rest_error', None)
            try:
                if fetch is None:
                    child_env = {k: v for k, v in env.items() if k != 'GH_DEBUG'}
                    response = subprocess.run(['gh', 'api', '--hostname', 'github.com', 'rate_limit'],
                        env=child_env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=12, check=True)
                    value = json.loads(response.stdout)
                else:
                    value = fetch()
                quotas = validate_quota(value)
                state['rest_graphql_estimate'] = quotas.pop('graphql')
            except Exception as error:
                if observed is None:
                    raise
                state['rest_error'] = type(error).__name__
            if observed is not None:
                quotas['graphql'] = observed
            state['graphql_observation'] = 'actual_response' if observed else 'unknown'
            scan, counts = {}, []
            state.pop('source_error', None)
            try:
                if sources is None:
                    if where is None:
                        peers = load_module('quota_peers', 'herdr_peers.py')
                        where = peers.all_panes(env)
                    sources = find_sources(env, where, now)
                counts = attribution(sources, now - 3600, diagnostics=scan)
            except Exception as error:
                state['source_error'] = type(error).__name__
            state.update(status='partial' if state.get('rest_error') else 'ok', sampled_at=now, resources=quotas, sources=counts, source_scan=scan,
                         attribution='visible gh commands in last hour; opaque Codex exec / Claude REPL, HTTP requests, jobs and other hosts not attributable')
            state.pop('error', None)
            low = {k: v for k, v in quotas.items() if v['limit'] and v['remaining'] < v['limit'] * .2 and v['reset'] > now}
            keys = [f'{name}:{item["reset"]}' for name, item in low.items()]
            if keys and any(key not in state.get('notified', []) for key in keys):
                numbers = ', '.join(f'{name} {q["remaining"]}/{q["limit"]}' for name, q in low.items())
                known = [r for r in counts if r['gh_commands']][:3]
                busiest = ', '.join(f'{r["source"]} {r["gh_commands"]}회' for r in known) or '없음'
                coverage = ('출처 집계 실패: 사용 출처 미확인. ' if state.get('source_error') else
                            f'기록 {scan["scanned_files"]}/{scan["candidate_files"]}개 읽음(일부 {scan["partial_files"]}개). '
                            f'미계측 스크립트 호출 {scan["unparsed_script_calls"]}회. ')
                state['pending'] = dict(keys=keys, expires=min(q['reset'] for q in low.values()), message=(
                    f'[GitHub 한도] 잔여량 20% 미만: {numbers}. '
                    + ('GraphQL은 실제 GraphQL 응답 기준입니다. ' if observed else '실제 GraphQL 응답 기록은 없습니다. ')
                    + f'최근 1시간 관측 gh 명령: {busiest}. '
                    + coverage + 'Codex exec·Claude REPL 안의 gh는 세지 않았습니다. '
                    '한정된 로컬 기록의 추정치이며 HTTP 요청 수가 아닙니다. 잡·다른 호스트 등 관측 밖 출처는 알 수 없습니다.'))
            elif not low:
                state.pop('pending', None)
            deliver()
        except Exception as error:
            state.update(status='error', error=type(error).__name__)
            # Previous numeric values retain their own sampled_at; never fabricate zero.
        write_json(folder / 'github-quota.json', state)


def append_context(value, note):
    if not note:
        return value
    result = json.loads(value) if value else dict(hookSpecificOutput=dict(hookEventName='SessionStart', additionalContext=''))
    hook = result['hookSpecificOutput']
    hook['additionalContext'] = (hook.get('additionalContext', '') + '\n' + note).strip()
    return json.dumps(result, ensure_ascii=False)


if __name__ == '__main__':
    try:
        if sys.argv[1] == 'start':
            payload = json.load(sys.stdin)
            previous = dict(transcript_path=sys.argv[2]) if len(sys.argv) > 2 else None
            print(session_start(payload, dict(os.environ), previous))
        elif sys.argv[1] == 'append':
            print(append_context(sys.stdin.read(), sys.argv[2]))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        pass  # telemetry must never block a handoff
