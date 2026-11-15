#!/usr/bin/env python3
"""Read-only preflight for observed check restrictions; never runs a probe."""
import ast
import json
from pathlib import Path
import re
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


def requirement(text):
    """Only commands observed failing, not generic service/process keywords."""
    patterns = [
        ('BROWSER_DENIED', r'\bego-browser\b[^\n`]*'),
        ('BROWSER_DENIED', r'(?:실제|현재|로그인)[^\n.]{0,16}화면|브라우저[^\n.]{0,24}(?:QA|확인|검증)|(?:렌더링|DOM|스크린샷)[^\n.]{0,16}(?:확인|검증)'),
        ('OTHER_DENIED', r'\bcrontab\s+-l\b[^\n`]*'),
        ('OTHER_DENIED', r'\bsandbox-exec\b[^\n`]*'),
        ('OTHER_DENIED', r'\bcodex\s+app-server\b[^\n`]*'),
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
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = ast.unparse(node.func)
            if name in ('os.kill', 'os.killpg'):
                return 'SIGNAL_DENIED', f'{path}: {ast.unparse(node)[:256]}'
            if name not in ('subprocess.run', 'subprocess.Popen', 'subprocess.call', 'subprocess.check_call', 'subprocess.check_output') or not node.args:
                continue
            try:
                argv = ast.literal_eval(node.args[0])
            except (ValueError, TypeError):
                continue
            if isinstance(argv, (list, tuple)) and argv and isinstance(argv[0], str):
                exe = Path(argv[0]).name
                if exe in ('ps', 'pgrep', 'kill', 'killall'):
                    return 'SIGNAL_DENIED', f'{path}: {argv!r}'
                found = requirement(' '.join(str(arg) for arg in argv))
                if found:
                    return found[0], f'{path}: {found[1]}'
        return None
    source = re.sub(r'^\s*#.*$', '', source, flags=re.M)
    match = re.search(r'(?:^|[;|(&\n])\s*(?:/(?:usr/)?bin/)?(?:ps|pgrep|kill|killall)\s+[^\n;]*', source)
    # Shell tests can contain Python heredocs with the same observed Popen restriction.
    nested = re.search(r'subprocess\.(?:run|Popen|check_output|call|check_call)\s*\(\s*\[\s*[\'"](?:/(?:usr/)?bin/)?(?:ps|pgrep)[\'"]', source)
    if match or nested:
        return 'SIGNAL_DENIED', f'{path}: {(match or nested).group(0).strip()[:256]}'
    found = requirement(source)
    return (found[0], f'{path}: {found[1]}') if found else None


def classify(text, cwd):
    result = dict(kind='auto', reason='Codex: no observed sandbox requirement', blocked_reason='', blocked_command='')
    blocked = blockage(text)
    if blocked:
        if blocked['retry_target'] == 'claude':
            return dict(result, kind='claude', reason='reported permission requirement', **{k: blocked[k] for k in FIELDS[:2]})
        return result  # Shared guards/account access need the lead, not a runner change.
    found = requirement(text)
    if not found:
        # Read only named scripts, at most 32 files of 256 KiB. Missing evidence keeps the default.
        names = re.findall(r'(?:~?/|\.{1,2}/)?[\w.@/+\-]+\.(?:py|sh)|(?:[\w.@/+\-]*/)?pre-push\b', text)
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
        result.update(kind='claude', reason='observed sandbox requirement: ' + found[0],
                      blocked_reason=found[0], blocked_command=found[1])
    return result


if __name__ == '__main__':
    if sys.argv[1:] == ['--contract']:
        print(CONTRACT)
    elif len(sys.argv) == 2:
        print(json.dumps(classify(sys.stdin.read(1048576), sys.argv[1]), ensure_ascii=False))
    else:
        raise SystemExit('usage: check-permissions.py CWD | --contract')
