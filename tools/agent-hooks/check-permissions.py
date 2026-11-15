#!/usr/bin/env python3
"""Read-only preflight for observed check restrictions; never runs a probe."""
import ast
import json
from pathlib import Path
import re
import shlex
import sys

REASONS = ('SIGNAL_DENIED', 'SOCKET_DENIED', 'LAUNCHCTL_DENIED', 'BROWSER_DENIED', 'OTHER_DENIED')
FIELDS = ('blocked_reason', 'blocked_command', 'retry_target')
CONTRACT = (' When a required check is blocked by permissions, use TASK_CHECK: unknown unless an independent code or '
            'scope failure was observed; keep that independent fail. Add three unquoted, single-line fields: '
            'blocked_reason: SIGNAL_DENIED|SOCKET_DENIED|LAUNCHCTL_DENIED|BROWSER_DENIED|OTHER_DENIED, '
            'blocked_command: the exact blocked command (redact secret values), retry_target: claude|codex|lead. '
            'Use one actual reason, not the pipe-separated list. Include the original error in the full report. '
            'Use lead for shared safety hooks or account/IAM denials; never bypass them. '
            'Omit these fields when no permissions blocked a check. A permissions block is not a code failure.')


def blockage(text):
    """Only a complete, unambiguous result outside code fences and quotes."""
    lines, fence = [], None
    for line in text.splitlines():
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line)
        if marker:
            run, tail = marker.groups()
            if fence is None:
                fence = run
            elif run[0] == fence[0] and len(run) >= len(fence) and not tail.strip():
                fence = None
            continue
        if fence is None:
            lines.append(line)
    text = '\n'.join(lines)
    values = {}
    for key in FIELDS:
        matches = re.findall(r'^' + key + r':[ \t]*([^\n]*)$', text, re.M)
        if len(matches) != 1 or not matches[0].strip() or len(matches[0]) > 4096:
            return {}
        values[key] = matches[0].strip()
    if values['blocked_reason'] not in REASONS or values['retry_target'] not in ('claude', 'codex', 'lead'):
        return {}
    return values


def execution_text(text):
    # Search/read arguments describe text, not commands to execute. Preserve shell
    # separators so a later real browser command on the same line still counts.
    return re.sub(r'''\b(?:rg|grep|cat|head|tail)\s+(?:"[^"\n]*"|'[^'\n]*'|[^;&|`\n'"])+''', ' ', text)


def requirement(text):
    """Only commands observed failing, not generic service/process keywords."""
    text = execution_text(text)
    start = r'(?:(?:^|[;`|\n])\s*|\b(?:run|use|using|via)\s+)'
    patterns = [
        ('BROWSER_DENIED', start + r'ego-browser(?=$|\s)[^\n`]*'),
        ('BROWSER_DENIED', r'(?:실제|현재|로그인)[^\n.]{0,16}화면|브라우저[^\n.]{0,24}(?:QA|확인|검증)|(?:렌더링|DOM|스크린샷)[^\n.]{0,16}(?:확인|검증)'),
        ('OTHER_DENIED', start + r'crontab\s+-l(?=$|\s)[^\n`]*'),
        ('OTHER_DENIED', start + r'sandbox-exec(?=$|\s)[^\n`]*'),
        ('OTHER_DENIED', start + r'codex\s+app-server(?=$|\s)[^\n`]*'),
    ]
    for reason, pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return reason, match.group(0)[:512]
    return None


def source_requirement(path, source):
    if path.suffix == '.py':
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError):
            return None
        aliases = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                aliases.update((item.asname or item.name, item.name) for item in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                aliases.update((item.asname or item.name, node.module + '.' + item.name) for item in node.names)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, (ast.Name, ast.Attribute)):
                head, dot, tail = ast.unparse(node.value).partition('.')
                value = aliases.get(head, head) + (dot + tail if dot else '')
                aliases.update((target.id, value) for target in node.targets if isinstance(target, ast.Name))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = ast.unparse(node.func)
            head, dot, tail = name.partition('.')
            name = aliases.get(head, head) + (dot + tail if dot else '')
            if name in ('os.kill', 'os.killpg'):
                return 'SIGNAL_DENIED', f'{path}: {ast.unparse(node)[:256]}'
            process = (name in ('subprocess.run', 'subprocess.Popen', 'subprocess.call',
                                'subprocess.check_call', 'subprocess.check_output',
                                'subprocess.getoutput', 'subprocess.getstatusoutput',
                                'os.system', 'os.popen', 'asyncio.create_subprocess_exec',
                                'asyncio.create_subprocess_shell', 'exec', 'builtins.exec')
                       or re.fullmatch(r'os\.(?:exec[lv]p?e?|spawn[lv]p?e?|posix_spawnp?)', name))
            if not process:
                continue
            try:
                if name in ('exec', 'builtins.exec') or any(key.arg is None for key in node.keywords):
                    raise ValueError('dynamic execution')
                args = node.args or [key.value for key in node.keywords if key.arg in ('args', 'command', 'cmd', 'path', 'file', 'program')]
                if not args:
                    raise ValueError('missing command')
                # Resolve literals only. Variables, starred arguments, wrappers and
                # executable overrides can hide the observed app-server/ps calls.
                values = [ast.literal_eval(arg) for arg in args]
                argv = values[0]
                if re.fullmatch(r'os\.(?:exec[lv]p?e?|spawn[lv]p?e?|posix_spawnp?)', name):
                    offset = 1 if name.startswith('os.spawn') else 0
                    executable = values[offset]
                    method = name.rsplit('.', 1)[1]
                    arguments = (values[offset + 1] if 'v' in method or method.startswith('posix_spawn')
                                 else values[offset + 1:-1] if method.endswith('e') else values[offset + 1:])
                    if not isinstance(arguments, (list, tuple)):
                        raise ValueError('unresolved exec arguments')
                    argv = [executable, *arguments[1:]]
                elif name == 'asyncio.create_subprocess_exec':
                    argv = values
                shell = name in ('os.system', 'os.popen', 'subprocess.getoutput',
                                 'subprocess.getstatusoutput', 'asyncio.create_subprocess_shell')
                shell_option = next((key.value for key in node.keywords if key.arg == 'shell'), None)
                if shell_option is not None:
                    shell = bool(ast.literal_eval(shell_option))
                if shell:
                    payload = argv[0] if isinstance(argv, (list, tuple)) else argv
                    if not isinstance(payload, str):
                        raise ValueError('unresolved shell command')
                    found = shell_requirement(path, payload)
                    if found:
                        return found
                if isinstance(argv, str):
                    if re.search(r'[$`]', argv):
                        raise ValueError('shell expansion')
                    argv = shlex.split(argv)
                if not isinstance(argv, (list, tuple)) or not argv or not all(isinstance(arg, str) for arg in argv):
                    raise ValueError('unresolved argv')
                override = next((key.value for key in node.keywords if key.arg == 'executable'), None)
                if override is not None:
                    executable = ast.literal_eval(override)
                    if executable is not None:
                        if not isinstance(executable, str):
                            raise ValueError('unresolved executable')
                        argv = [executable, *argv[1:]]
                if Path(argv[0]).name in ('sh', 'bash', 'zsh'):
                    command_index = next((i + 1 for i, arg in enumerate(argv[1:], 1)
                                          if re.fullmatch(r'-[a-zA-Z]*c[a-zA-Z]*', arg)), None)
                    if command_index is not None:
                        command = argv[command_index]
                        if re.search(r'[$`]', command):
                            raise ValueError('shell expansion')
                        found = shell_requirement(path, command)
                        if found:
                            return found
            except (ValueError, TypeError, IndexError):
                return 'SIGNAL_DENIED', f'{path}: unresolved process launch: {ast.unparse(node)[:256]}'
            if isinstance(argv, (list, tuple)) and argv and isinstance(argv[0], str):
                exe = Path(argv[0]).name
                if exe in ('ps', 'pgrep', 'kill', 'killall'):
                    return 'SIGNAL_DENIED', f'{path}: {argv!r}'
                found = requirement(' '.join([exe, *argv[1:]]))
                if found:
                    return found[0], f'{path}: {found[1]}'
        return None
    return shell_requirement(path, source)


def shell_requirement(path, source):
    # Tokenize without executing. A semicolon or $COMMAND inside an echo/grep
    # argument is data; shell wrappers explicitly interpret their payload.
    lexer = shlex.shlex(source, posix=True, punctuation_chars=';&|()\n')
    lexer.whitespace, lexer.whitespace_split = ' \t\r', True
    try:
        tokens = list(lexer)
    except ValueError:
        return None
    commands, command = [], []
    for token in [*tokens, '\n']:
        if re.fullmatch(r'[;&|()\n]+', token):
            if command:
                commands.append(command)
            command = []
        else:
            command.append(token)
    for command in commands:
        while command and command[0] in ('exec', 'command', 'builtin'):
            command = command[1:]
            if command and command[0] == '--':
                command = command[1:]
        if not command:
            continue
        exe = Path(command[0]).name
        if command[0].startswith(('$', '`')) or exe == 'eval':
            return 'SIGNAL_DENIED', f'{path}: unresolved process launch: {shlex.join(command)[:256]}'
        if exe in ('ps', 'pgrep', 'kill', 'killall'):
            return 'SIGNAL_DENIED', f'{path}: {shlex.join(command)[:256]}'
        if exe in ('sh', 'bash', 'zsh'):
            index = next((i + 1 for i, arg in enumerate(command[1:], 1)
                          if re.fullmatch(r'-[a-zA-Z]*c[a-zA-Z]*', arg)), None)
            if index is not None:
                if index >= len(command):
                    return 'SIGNAL_DENIED', f'{path}: unresolved process launch: {shlex.join(command)[:256]}'
                found = shell_requirement(path, command[index])
                if found:
                    return found
        if exe in ('ego-browser', 'crontab', 'sandbox-exec', 'codex'):
            found = requirement(' '.join([exe, *command[1:]]))
            if found:
                return found[0], f'{path}: {found[1]}'
    # Shell tests can contain Python heredocs with the same observed Popen restriction.
    nested = re.search(r'subprocess\.(?:run|Popen|check_output|call|check_call)\s*\(\s*\[\s*[\'"](?:/(?:usr/)?bin/)?(?:ps|pgrep)[\'"]', source)
    if nested:
        return 'SIGNAL_DENIED', f'{path}: {nested.group(0).strip()[:256]}'
    return None


def classify(text, cwd):
    result = dict(kind='auto', reason='Codex: no observed sandbox requirement', blocked_reason='', blocked_command='')
    blocked = blockage(text)
    if blocked:
        if blocked['retry_target'] == 'claude':
            return dict(result, kind='claude', reason='reported permission requirement', **{k: blocked[k] for k in FIELDS[:2]})
        return result  # Shared guards/account access need the lead, not a runner change.
    text = execution_text(text)
    found = requirement(text)
    if not found:
        # Read only named scripts, at most 32 files of 256 KiB. Missing evidence keeps the default.
        script = r'(?:~?/|\.{1,2}/)?[\w.@/+\-]+\.(?:py|sh)|(?:[\w.@/+\-]*/)?pre-push\b'
        invocation = r'(?:\b(?:python(?:3(?:\.\d+)?)?|bash|sh)\s+(?:-\S+\s+)*|(?:^|[`;\n])\s*(?:Run\s+)?)'
        # A source named for reading/diff review does not imply running its tests.
        names = re.findall(invocation + '(' + script + ')', text, flags=re.I)
        for name in dict.fromkeys(names[:32]):
            path = Path(name).expanduser()
            if not path.is_absolute():
                path = Path(cwd) / path
            try:
                if not path.is_file() or path.stat().st_size > 262144:
                    continue
                source = path.read_text(errors='replace')
            except OSError:
                continue
            # This repository's full suite includes the demonstrated nested ps tests.
            if (path.name == 'pre-push' and path.parent.name == 'git-hooks'
                    and "find tools" in source and "*.test.py" in source and "setpgid(0, 0)" in source):
                found = ('SIGNAL_DENIED', f'bash {path}: full suite includes nested process reads')
            else:
                found = source_requirement(path, source)
            if found:
                break
    if found:
        reason = 'unresolved process requirement' if 'unresolved process launch:' in found[1] else 'observed sandbox requirement: ' + found[0]
        result.update(kind='claude', reason=reason,
                      blocked_reason=found[0], blocked_command=found[1])
    return result


if __name__ == '__main__':
    if sys.argv[1:] == ['--contract']:
        print(CONTRACT)
    elif len(sys.argv) == 2:
        print(json.dumps(classify(sys.stdin.read(1048576), sys.argv[1]), ensure_ascii=False))
    else:
        raise SystemExit('usage: check-permissions.py CWD | --contract')
