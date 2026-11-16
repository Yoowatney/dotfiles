"""Require a current human request for clipboard writes in either runtime.

This is a tool-input guard, not a sandbox: arbitrary scripts can hide a write.
Only the fixed non-shell GCP transfer runner needs no request.
No environment flag or assistant-supplied reason grants permission.
"""
import importlib.util
from pathlib import Path
import re
import shlex

DENY = ('Clipboard write refused: the current human turn did not explicitly request it. '
        'Deliver the draft in the requested conversation thread. '
        'Do not ask for clipboard permission merely to work around this refusal.')


def direct_secret_copy(command):
    """Only the fixed runner; raw pipelines inherit untrusted shell/SDK state.

    The launch prefix bypasses aliases/functions for the source and sink names.
    The runner uses shell=False, fixed binaries, and a closed environment.
    """
    helper = str(Path(__file__).resolve().with_name('secret-clipboard.py'))
    prefix = r'\/usr/bin/env /usr/bin/python3 -I ' + helper + ' '
    if not command.startswith(prefix):
        return False
    args = command[len(prefix):]
    return bool(re.fullmatch(
        r'gcloud --project [a-z][a-z0-9-]{4,62} --secret [A-Za-z0-9_-]{1,255}'
        r'(?: --version (?:latest|[0-9]+))?', args))


def unwrap(words):
    """Recognize common launchers without treating text-search arguments as code."""
    words = list(words)
    while words:
        if re.match(r'^[A-Za-z_]\w*=', words[0]):
            words.pop(0)
            continue
        name = Path(words[0]).name
        if name in ('command', 'builtin'):
            words.pop(0)
            if words and words[0] == '--':
                words.pop(0)
        elif name in ('env', 'sudo'):
            words.pop(0)
            while words and words[0].startswith('-'):
                option = words.pop(0)
                if option == '--':
                    break
                if option in ('-u', '--unset', '-C', '--chdir', '-g', '--group', '--user') and words:
                    words.pop(0)
        elif name in ('timeout', 'gtimeout'):
            words.pop(0)
            while words and words[0].startswith('-'):
                option = words.pop(0)
                if option in ('-s', '--signal', '-k', '--kill-after') and words:
                    words.pop(0)
            words = words[1:]  # duration
        elif name == 'uv' and len(words) > 1 and words[1] == 'run':
            words = words[2:]
            while words and words[0].startswith('-'):
                option = words.pop(0)
                if option == '--':
                    break
                if option in ('--directory', '--project', '--python', '--with', '--with-requirements') and words:
                    words.pop(0)
        else:
            break
    return words


def writes_clipboard(command, depth=0):
    if depth > 8:
        return True
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars='|;&()\n')
        lexer.whitespace = ' \t\r'
        tokens = list(lexer)
    except ValueError:
        # Unreadable shell text naming a clipboard writer must not slip through.
        return bool(re.search(r'pbcopy|slackcopy|pyperclip|clipboard', command, re.I))
    parts, part = [], []
    for token in tokens + [';']:
        if token and all(c in '|;&()\n' for c in token):
            if part:
                parts.append(part)
                part = []
        else:
            part.append(token)
    for words in parts:
        words = unwrap(words)
        if not words:
            continue
        name, args = Path(words[0]).name, words[1:]
        if name in ('pbcopy', 'slackcopy'):
            if name == 'slackcopy' and args == ['--selftest']:
                continue
            return True
        if name in ('sh', 'bash', 'zsh', 'dash', 'ksh'):
            for i, arg in enumerate(args[:-1]):
                if re.fullmatch(r'-[a-z]*c[a-z]*', arg) and writes_clipboard(args[i + 1], depth + 1):
                    return True
        if any(Path(arg).name == 'secret-clipboard.py' for arg in args):
            return True
        if re.fullmatch(r'python(?:\d+(?:\.\d+)?)?', name):
            code = ' '.join(args)
            if re.search(r'pyperclip\s*\.\s*copy\s*\(|(?:set_clipboard|write_clipboard)\s*\(', code):
                return True
            if '-c' in args and re.search(r'\b(?:pbcopy|slackcopy)\b', code):
                return True
        if name == 'osascript' and re.search(r'set\s+(?:the\s+)?clipboard\s+to\b', ' '.join(args), re.I):
            return True
        # A substitution inside a quoted argument still executes.
        for word in words:
            for nested in re.findall(r'\$\(([^()]*)\)|`([^`]*)`', word):
                if writes_clipboard(nested[0] or nested[1], depth + 1):
                    return True
    return False


def requested(prompt):
    """A direct imperative, never quoted policy, a relay or a standing instruction."""
    fence = None
    for line in prompt.splitlines():
        line = line.strip()
        if line.startswith('>'):
            continue
        if fence is not None:
            if re.fullmatch(re.escape(fence[0]) + '{' + str(fence[1]) + ',}', line):
                fence = None
            continue
        marker = re.match(r'(`{3,}|~{3,})', line)
        if marker:
            # Only the same marker of at least this length closes the block.
            fence = (marker[0][0], len(marker[0]))
            continue
        if re.search(r'[`"“”<>]|넣지|복사하지|하지\s*마|말고|않|아니|\b(?:not|never|don.t)\b', line, re.I):
            continue
        if re.fullmatch(r'(?:(?:이|그|저)\s*[^.!?\n]{0,40}?\s+)?클립보드에\s*'
                        r'(?:넣어|복사해|담아)(?:\s*(?:줘|주세요|주세요요|줄래|주실래요|주세요\.)|줘요)?[.!?]?', line):
            return True
        if re.fullmatch(r'(?:please\s+|can you\s+|could you\s+)?(?:copy|put)\s+[^.!?\n]{1,120}?\s+'
                        r'(?:to|into|on)\s+(?:the\s+)?clipboard(?:,?\s*please)?[.!?]?', line, re.I):
            return True
    return False


def reason(payload):
    tool, inp = payload.get('tool_name', ''), payload.get('tool_input') or {}
    if tool not in ('Bash', 'exec_command', 'shell_command', 'mcp__codex_tui__execute'):
        return None
    command = inp.get('command') or inp.get('cmd') or ''
    if not isinstance(command, str) or not writes_clipboard(command):
        return None
    if direct_secret_copy(command):
        return None
    try:
        # Reuse the identity guard's battle-tested Claude/Codex turn parsing.
        # This only reads the transcript; none of its identity exceptions apply.
        path = Path(__file__).with_name('gcp-account-guard.py')
        spec = importlib.util.spec_from_file_location('clipboard_human_turn', path)
        parser = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(parser)
        prompt = parser.latest_prompt(payload.get('transcript_path') or '')
    except Exception:
        return DENY
    return None if requested(prompt) else DENY
