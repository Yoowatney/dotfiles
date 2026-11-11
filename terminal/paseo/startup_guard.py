#!/usr/bin/env python3
"""Block Paseo startup without env -i; --install registers this hook in both harnesses.

The installer appends a group without moving existing Codex hook trust positions.
Run from the permanent checkout after review; tests use --home with a temporary directory.
This guards ordinary shell launches, not arbitrary programs that spawn processes.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import sys
import tempfile

SAFE_NAMES = {'HOME', 'USER', 'LOGNAME', 'PATH', 'TMPDIR', 'LANG'}
ASSIGNMENT = re.compile(r'^([A-Za-z_]\w*)=')
REASON = ('Paseo가 세션 환경을 물려받지 않도록 env -i HOME="$HOME" USER="$USER" '
          'PATH=/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin 뒤에서 시작하세요. '
          '이미 실행 중인 데몬의 환경은 새 실행으로 바뀌지 않습니다.')


def unsafe(command, inherited_clean=False, depth=0):
    if depth > 12:
        return 'paseo' in command.lower()
    lexer = shlex.shlex(command, posix=True, punctuation_chars=';&|()\n')
    lexer.whitespace = ' \t\r'
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return 'paseo' in command.lower()
    segments, current = [], []
    for token in tokens:
        if token and all(c in ';&|()\n' for c in token):
            if current:
                segments.append(current)
            current = []
        else:
            current.append(token)
    if current:
        segments.append(current)
    for words in segments:
        clean = inherited_clean
        if words and words[0] in {'export', 'declare', 'typeset'}:
            if any(ASSIGNMENT.match(word) and not allowed_assignment(word) for word in words[1:]):
                inherited_clean = False
            continue
        if words and all(ASSIGNMENT.match(word) for word in words):
            if any(not allowed_assignment(word) for word in words):
                inherited_clean = False
            continue
        # env and executable wrappers may alternate in either order.
        while words:
            if ASSIGNMENT.match(words[0]):
                if not allowed_assignment(words.pop(0)):
                    clean = False
            elif Path(words[0]).name in {'command', 'exec', 'nohup', 'sudo'}:
                words.pop(0)
                while words and words[0].startswith('-'):
                    words.pop(0)
            elif Path(words[0]).name == 'env':
                words.pop(0)
                clear = False
                assignments_safe = True
                while words:
                    word = words[0]
                    if word in {'-i', '--ignore-environment'}:
                        clear = True
                    elif ASSIGNMENT.match(word):
                        assignments_safe &= allowed_assignment(word)
                    elif word == '--':
                        words.pop(0)
                        break
                    elif word.startswith('-'):
                        # Unknown env options are not proof of a clean launch.
                        assignments_safe = False
                    else:
                        break
                    words.pop(0)
                clean = (clear or clean) and assignments_safe
            else:
                break
        if not words:
            continue
        binary = Path(words[0]).name
        args = words[1:]
        if binary in {'bash', 'sh', 'zsh'}:
            for i, arg in enumerate(args[:-1]):
                if arg.startswith('-') and 'c' in arg[1:]:
                    if unsafe(args[i + 1], clean, depth + 1):
                        return True
        is_app = binary == 'Paseo'
        if binary == 'open':
            options_ended = False
            for i, arg in enumerate(args):
                if arg == '--args' and not options_ended:
                    break
                if arg == '--':
                    options_ended = True
                    continue
                app = None
                if not options_ended:
                    if arg == '--env':
                        if i + 1 >= len(args) or not allowed_assignment(args[i + 1]):
                            clean = False
                    elif arg.startswith('--env='):
                        if not allowed_assignment(arg.split('=', 1)[1]):
                            clean = False
                    if arg in {'-a', '--application'} and i + 1 < len(args):
                        app = args[i + 1]
                    elif arg.startswith('--application='):
                        app = arg.split('=', 1)[1]
                    elif arg.startswith('-a'):
                        app = arg[2:]
                if app and Path(app.rstrip('/')).name.lower() in {'paseo', 'paseo.app'}:
                    is_app = True
                if arg.rstrip('/').endswith('/Paseo.app'):
                    is_app = True
        is_daemon = binary == 'paseo' and args[:2] == ['daemon', 'start']
        if (is_app or is_daemon) and not clean:
            return True
    return False


def allowed_assignment(word):
    name = word.split('=', 1)[0]
    return name in SAFE_NAMES or name.startswith('LC_')


def install(home):
    command = 'python3 -I ' + shlex.quote(str(Path(__file__).resolve()))
    prepared = []
    for path in [home / '.claude/settings.json', home / '.codex/hooks.json']:
        data = json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(data, dict):
            raise ValueError('settings must be an object')
        hooks = data.setdefault('hooks', {})
        groups = hooks.setdefault('PreToolUse', [])
        if not isinstance(groups, list):
            raise ValueError('PreToolUse must be a list')
        installed = False
        for group in groups:
            for hook in group['hooks']:
                if hook.get('command') == command:
                    installed = True
        if not installed:
            groups.append({'matcher': 'Bash', 'hooks': [{'type': 'command', 'command': command, 'timeout': 10}]})
        prepared.append((path, json.dumps(data, ensure_ascii=False, indent=2) + '\n'))
    # Parse and validate both first. A malformed second config must not change the first.
    for path, content in prepared:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                stream.write(content)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', action='store_true')
    parser.add_argument('--home', type=Path, default=Path.home())
    options = parser.parse_args()
    if options.install:
        install(options.home)
        return
    payload = json.load(sys.stdin)
    if payload.get('tool_name') != 'Bash':
        return
    tool_input = payload.get('tool_input') or {}
    command = tool_input.get('command') or tool_input.get('cmd') or ''
    if unsafe(command):
        print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse',
            'permissionDecision': 'deny', 'permissionDecisionReason': REASON}}, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, TypeError, KeyError, AttributeError) as error:
        print('Paseo startup guard: ' + str(error), file=sys.stderr)
        sys.exit(1)
