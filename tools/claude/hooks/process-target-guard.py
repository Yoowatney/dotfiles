#!/usr/bin/env python3
"""Read-only preflight of common shell process selectors; never sends signals.

This is an accident guard, not a shell sandbox. Dynamic command names and code
inside external scripts are outside its scope. Unknown selector options and
unresolved selector values fail closed for commands this guard recognizes.
"""
import os
import re
import signal
import shlex
import subprocess
import sys
import time

PROTECTED = {'claude', 'codex', 'herdr'}
TOOLS = {'pkill', 'killall', 'pgrep', 'kill'}
SHELLS = {'sh', 'bash', 'zsh'}
RUNNERS = {'env', 'nohup', 'nice', 'timeout', 'gtimeout', 'xargs', 'caffeinate', 'sudo', 'exec', 'time'}
VALUED = {'exec': {'-a'}, 'time': set(), 'env': {'-u', '--unset', '-C', '--chdir'},
          'sudo': {'-u', '-g', '-h', '-p', '-C', '-D', '-R', '-T',
                   '--user', '--group', '--host', '--prompt', '--close-from',
                   '--chdir', '--chroot', '--command-timeout'},
          'caffeinate': {'-w', '-t'},
          'nice': {'-n'}, 'timeout': {'-s', '-k', '--signal', '--kill-after'},
          'gtimeout': {'-s', '-k', '--signal', '--kill-after'},
          'xargs': {'-E', '-I', '-J', '-L', '-n', '-P', '-R', '-S', '-s', '-d', '--eof', '--replace'}}
FLAGS = {'exec': set('cl'), 'time': {'p'}, 'env': {'i', '0', 'v'}, 'sudo': set('AbEHkKlnNPSVv'),
         'nice': set(), 'timeout': set('v'), 'gtimeout': set('v'),
         'xargs': set('0oprtx'), 'caffeinate': set('dimsu'), 'nohup': set()}
SIGNALS = {s.name.removeprefix('SIG') for s in signal.Signals}
PREFIX = 'process-target-guard: '
EXPANSION = '\ue000'
QUOTED_META = {'$': '\ue001', '`': '\ue002'}  # $ and ` inside single quotes are literal
UNMASK = str.maketrans({v: k for k, v in QUOTED_META.items()})
KILL_WORD = re.compile(r'\b(?:pkill|killall|kill)\b')


def name(word):
    return os.path.basename(word.lstrip('(')).strip('()')


def unwrap(words):
    w = words
    while w:
        executable = name(w[0])
        if re.match(r'^[A-Za-z_]\w*=', w[0]) or executable in {
                'then', 'do', 'else', 'elif', 'if', 'while', 'until', '!', '{', '', 'noglob', 'nocorrect'}:
            w = w[1:]
        elif executable in {'command', 'builtin'}:
            w = w[1:]
            while w and w[0] in {'-p', '--'}:
                w = w[1:]
            if w and w[0] in {'-v', '-V'}:
                return []  # command lookup never executes its operand
            if w and w[0].startswith('-'):
                raise ValueError('unsupported command prefix option')
        elif executable in RUNNERS:
            runner, w = executable, w[1:]
            while w and (w[0].startswith('-') or re.match(r'^[A-Za-z_]\w*=', w[0])
                         or re.fullmatch(r'[\d.]+[smhd]?', w[0])):
                option = w[0]
                if runner == 'xargs' and (option[:2] in {'-I', '-J'} or option.split('=', 1)[0] == '--replace'):
                    raise ValueError('xargs replacement strings cannot be resolved')
                if option in VALUED.get(runner, set()):
                    if len(w) < 2:
                        raise ValueError('missing wrapper option value')
                    w = w[2:]
                elif runner == 'xargs' and len(option) > 2 and option[:2] in VALUED['xargs']:
                    w = w[1:]
                elif option.startswith('--') and option.split('=', 1)[0] in VALUED.get(runner, set()) and '=' in option:
                    w = w[1:]
                elif option in {'-S', '--split-string'} and runner == 'env':
                    raise ValueError('env split-string requires an explicit command')
                elif (option.startswith('-') and option != '--'
                      and not (set(option[1:]) <= FLAGS[runner]
                               or runner == 'nice' and option[1:].isdigit()
                               or option in {'--no-run-if-empty', '--ignore-environment',
                                             '--preserve-status', '--foreground', '--verbose'})):
                    raise ValueError('unsupported command wrapper option')
                else:
                    w = w[1:]
        else:
            break
    return w


def segments(text, parser):
    """Keep redirections out of executable names and selector arguments."""
    text = parser.strip_comments(text.replace('\\\n', ' '))
    text = parser.QUOTED.sub(lambda m: re.sub(r"\\([$`])", r"\1", m[0])
                             if m[0][0] == '"' else m[0], text)
    lexer = shlex.shlex(text, posix=True, punctuation_chars='();&|<>\n')
    lexer.whitespace = ' \t\r'
    lexer.whitespace_split, lexer.commenters = True, ''
    tokens = []
    for token in lexer:
        if token and set(token) <= set('();&|<>\n'):
            tokens.extend(re.findall(r'[()]|\n|[;&|<>]+', token))
        else:
            tokens.append(token)
    tokens = iter(tokens)
    current, here_strings = [], []
    for token in tokens:
        if token == '<<<':
            body = next(tokens, None)
            if body is None:
                raise ValueError('missing here-string body')
            here_strings.extend(['PROCESS_HERE_STRING', body])
        elif re.fullmatch(r'[<>]+&?|&>{1,2}', token):
            if current and current[-1].isdigit():
                current.pop()
            if next(tokens, None) is None:
                raise ValueError('missing redirection target')
        elif token and set(token) <= set(';&|\n'):
            yield current + here_strings
            current, here_strings = [], []
        else:
            current.append(token)
    yield current + here_strings


def mask_expansions(text, parser):
    """Keep quote context until glob expansion can be marked as unresolved."""
    out, quote, i = [], '', 0
    while i < len(text):
        ch = text[i]
        if ch == '\\' and quote != "'" and i + 1 < len(text):
            out.append(text[i:i + 2]); i += 2
            continue
        if ch in "'\"" and (not quote or quote == ch):
            quote = '' if quote else ch
        if quote != "'":
            capture = parser.CAPTURE.match(text, i)
            if capture:
                out.append('PROCESS_CAPTURE'); i = capture.end()
                continue
        group_brace = ch == '{' and (i + 1 == len(text) or text[i + 1].isspace())
        out.append(QUOTED_META[ch] if quote == "'" and ch in QUOTED_META
                   else EXPANSION + ch if not quote and ch in '*?[{~' and not group_brace else ch)
        i += 1
    return ''.join(out)


def commands(text, parser, depth=0):
    if depth > 8:
        raise ValueError('shell nesting is too deep')
    sources = []
    for here in parser.HEREDOC.finditer(text):
        header = text[:here.start()].rsplit('\n', 1)[-1]
        if any(w and name(w[0]) in SHELLS for w in map(unwrap, segments(header, parser))):
            yield from commands(here[3], parser, depth + 1)
        elif not re.match(r"<<-?\s*['\"\\]", here[0]):
            sources.extend(a or b for a, b in parser.CAPTURE.findall(here[3]))
    text = parser.strip_comments(parser.HEREDOC.sub('<<heredoc', text))
    # Only substitutions outside single quotes execute. Keep shell -c arguments
    # intact: stripping captures first would also erase the inner shell's code.
    quoted = re.compile(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"")
    expanding = quoted.sub(lambda m: "''" if m[0][0] == "'" else m[0], text)
    sources.extend(a or b for a, b in parser.CAPTURE.findall(expanding))
    sources.extend(re.findall(r'(?<!\\)[<>]\(((?:[^()]|\([^()]*\))*)\)', expanding))
    for source in sources:
        yield from commands(source, parser, depth + 1)
    case_depth = 0
    for words in segments(mask_expansions(text, parser), parser):
        try:
            w = unwrap(words)
        except ValueError:
            # Wrapper option strictness only matters when a kill tool may run inside.
            if KILL_WORD.search(' '.join(words).translate(UNMASK)):
                raise
            continue
        if w and name(w[0]) == 'case':
            case_depth += 1
            if 'in' not in w:
                raise ValueError('unsupported case statement')
            w = w[w.index('in') + 1:]
            if ')' not in w:
                continue
        if case_depth and w and name(w[0]) == 'esac':
            case_depth -= 1
            continue
        if case_depth and ')' in w and (not w or name(w[0]) not in TOOLS):
            w = unwrap(w[w.index(')') + 1:])
        if not w:
            continue
        executable = name(w[0])
        if executable == 'eval':
            yield from commands(' '.join(w[1:]).translate(UNMASK), parser, depth + 1)
        elif executable in SHELLS:
            for k, word in enumerate(w[:-1]):
                if word == 'PROCESS_HERE_STRING':
                    yield from commands(w[k + 1].translate(UNMASK), parser, depth + 1)
            for k, word in enumerate(w[1:-1], 1):
                if re.fullmatch(r'-[a-zA-Z]*c[a-zA-Z]*', word):
                    yield from commands(w[k + 1].translate(UNMASK), parser, depth + 1)
                    break
        else:
            yield executable, [arg for arg in w[1:] if arg not in {'(', ')'}]


def literal(value):
    if not value or re.search(r'[$`\n]', value) or EXPANSION in value or 'PROCESS_CAPTURE' in value:
        raise ValueError('selector contains an unresolved value')
    return value.translate(UNMASK)


def queries(tool, args):
    """Translate only reviewed selectors to argv for read-only pgrep."""
    options, patterns = [], []
    exact = tool == 'killall'
    i = 0
    while i < len(args):
        arg = literal(args[i])
        i += 1
        if arg == '--':
            patterns.extend(literal(a) for a in args[i:])
            break
        if not arg.startswith('-') or arg == '-':
            patterns.append(arg)
            continue
        sig = arg.lstrip('-').removeprefix('SIG')
        if tool != 'pgrep' and (sig.isdigit() or sig in SIGNALS):
            continue
        if tool == 'pkill' and arg in {'--signal', '-signal'}:
            if i >= len(args):
                raise ValueError('missing signal')
            literal(args[i]); i += 1
            continue
        if tool == 'pkill' and arg.startswith('--signal='):
            continue
        if tool == 'killall':
            if arg == '-m':
                exact = False
            elif arg == '-i':
                options.append('-i')
            elif arg in {'-v', '-q'}:
                pass
            elif arg == '-u' and i < len(args):
                options.extend(['-U', literal(args[i])]); i += 1
            else:
                raise ValueError('unsupported killall option')
            continue
        # macOS pgrep/pkill selectors. Strip output-only pgrep switches so the
        # result always contains PIDs. Unknown long/short options are refused.
        if arg.startswith('--'):
            raise ValueError('unsupported selector option')
        letters = arg[1:]
        k = 0
        while k < len(letters):
            flag = letters[k]
            k += 1
            if flag in 'fixvno' or (flag == 'a' and sys.platform == 'darwin'):
                options.append('-' + flag)
            elif flag in 'gGPstUuF':
                value = letters[k:] if k < len(letters) else (args[i] if i < len(args) else '')
                if k == len(letters):
                    i += 1
                # A pidfile could change between preflight and signal delivery.
                if flag == 'F':
                    raise ValueError('pidfile selectors require explicit reviewed PIDs')
                options.extend(['-' + flag, literal(value)])
                break
            elif tool == 'pgrep' and flag in 'alcq':
                raise ValueError('pgrep output format is not a PID list')
            elif tool == 'pkill' and flag in 'lI':
                pass
            elif tool == 'pgrep' and flag == 'd':
                raise ValueError('pgrep output delimiter can change kill targets')
            else:
                raise ValueError('unsupported selector option')
    if tool != 'killall' and len(patterns) > 1:
        raise ValueError('selector has multiple patterns')
    if not patterns and not options:
        raise ValueError('selector is empty')
    if exact:
        options.append('-x')
    if tool == 'killall' and sys.platform == 'darwin':
        options.insert(0, '-a')  # killall includes ancestors; Darwin pgrep does not
    return [['pgrep', *options, '--', p] for p in patterns] if patterns else [['pgrep', *options]]


def problem(command, parser):
    # Most hook calls avoid both parsing and process inspection.
    if 'kill' not in command:
        return None
    try:
        found = list(commands(command, parser))
        has_kill = any(tool == 'kill' for tool, _ in found)
        selectors = [(tool, args) for tool, args in found
                     if tool in {'pkill', 'killall'} or (tool == 'pgrep' and has_kill)]
        deadline = time.monotonic() + 4
        for tool, args in selectors:
            for query in queries(tool, args):
                timeout = max(.01, deadline - time.monotonic())
                result = subprocess.run(query, capture_output=True, text=True, timeout=timeout)
                if result.returncode == 1:
                    continue
                if result.returncode != 0:
                    raise ValueError('pgrep could not resolve targets')
                pids = result.stdout.split()
                if not pids or any(not p.isdecimal() for p in pids):
                    raise ValueError('pgrep returned invalid PIDs')
                result = subprocess.run(['ps', '-p', ','.join(pids), '-o', 'pid=,comm='],
                                        capture_output=True, text=True,
                                        timeout=max(.01, deadline - time.monotonic()))
                if result.returncode != 0:
                    raise ValueError('ps could not identify targets')
                protected = set()
                identified = set()
                for line in result.stdout.splitlines():
                    pid, sep, executable = line.strip().partition(' ')
                    if not sep or pid not in pids:
                        raise ValueError('ps returned an invalid target')
                    identified.add(pid)
                    if name(executable.strip()) in PROTECTED:
                        protected.add(name(executable.strip()))
                if identified != set(pids):
                    raise ValueError('ps did not identify every target')
                if protected:
                    return PREFIX + '종료 대상에 실행 중인 ' + ', '.join(sorted(protected)) + (
                        ' 프로세스가 있습니다. 명령을 거부합니다. 별도 작업의 PID를 확인하고 대상을 좁히세요.')
        if has_kill and selectors:
            if any(tool not in {'pgrep', 'kill', 'for', 'done', 'read', '}'} for tool, _ in found):
                raise ValueError('pgrep output passes through an unresolved command')
            for tool, args in found:
                if tool != 'kill':
                    continue
                args = list(args)
                if args and args[0] in {'-s', '-n'}:
                    args = args[2:]
                elif args and re.fullmatch(r'-(?:SIG)?(?:[A-Z]+|[0-9]+)', args[0]):
                    args = args[1:]
                if args and args[0] == '--':
                    args = args[1:]
                if any(a != 'PROCESS_CAPTURE' for a in args):
                    raise ValueError('kill has a process-group or unresolved target')
    except (ValueError, OSError, subprocess.SubprocessError):
        return PREFIX + '종료 대상을 안전하게 확인할 수 없어 거부합니다. 고정된 선택 조건으로 pgrep 조회부터 하세요.'
    return None
