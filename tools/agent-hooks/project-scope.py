#!/usr/bin/env python3
"""Project settings that Claude Code does not hand down by itself.

Claude reads .claude/settings.json only in the folder it starts in, and CLAUDE.local.md only there and above. A session
in a sub-repo would miss its project's plugins, and one in a git worktree elsewhere also its personal project rules.
This finds both: the parent folders under HOME, and for a worktree its main checkout's folders under HOME.
~/.config/agent-harness/version holding v0.1.0 turns all of it off (the layout before project scopes).

Usage: project-scope.py DIR  ->  {"enabledPlugins": {...}, "localRules": [...]}
Only file reads: no git command runs in the folder.
"""
import json
import os
from pathlib import Path
import sys


def under(path, home):
    """path and its parents, nearest first, strictly below home (home's own .claude is the user's settings)."""
    return [p for p in [path, *path.parents] if home in p.parents]


def main_checkout(cwd):
    for folder in [cwd, *cwd.parents]:
        dot = folder / '.git'
        if dot.is_dir():
            return None
        if dot.is_file():
            text = dot.read_text(errors='replace').strip()
            if not text.startswith('gitdir: '):
                return None
            gitdir = (folder / text[len('gitdir: '):]).resolve()  # <main>/.git/worktrees/<name>
            if gitdir.parent.name == 'worktrees' and gitdir.parent.parent.name == '.git':
                return gitdir.parent.parent.parent
            return None
    return None


def switched_off(home):
    try:
        return (home / '.config/agent-harness/version').read_text().strip() == 'v0.1.0'
    except (OSError, UnicodeError):
        return False


def plugins(folder, names=('settings.json',)):
    found = {}
    for name in names:
        try:
            found.update(json.loads((folder / '.claude' / name).read_text()).get('enabledPlugins') or {})
        except (OSError, ValueError, AttributeError, TypeError):
            pass  # A broken settings file never stops a launch.
    return found


def inherited(cwd, home):
    cwd, home = Path(cwd).resolve(), Path(home).resolve()
    if switched_off(home):
        return {}
    main = main_checkout(cwd)
    # Later wins: plain parents first, then the worktree's project, each outermost first.
    folders = [*reversed(under(cwd.parent, home)), *(reversed(under(main, home)) if main else [])]
    found = {}
    for folder in folders:
        found.update(plugins(folder))
    # Parents hand down only their shared settings.json: a settings.local.json is that one folder's own choice.
    for own in plugins(cwd, ('settings.json', 'settings.local.json')):  # Claude reads the start folder's files itself.
        found.pop(own, None)
    return found


def local_rules(cwd, home):
    """The main checkout chain's CLAUDE.local.md files that the folder's own parents do not already load."""
    cwd, home = Path(cwd).resolve(), Path(home).resolve()
    main = None if switched_off(home) else main_checkout(cwd)
    if not main:
        return []
    seen = {(p / 'CLAUDE.local.md').resolve() for p in [cwd, *cwd.parents]}
    root = next(p for p in [cwd, *cwd.parents] if (p / '.git').is_file())
    found = []
    for folder in reversed(under(main, home)):
        path = folder / 'CLAUDE.local.md'
        real = path.resolve()
        # A repo that tracks the file (or the one a link points to) gives the worktree its own copy.
        own = main in real.parents and (root / real.relative_to(main)).is_file()
        if path.is_file() and real not in seen and not own:
            seen.add(real)
            found.append(path)
    return found


if __name__ == '__main__':
    cwd = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    home = os.environ.get('HOME') or str(Path.home())
    print(json.dumps({'enabledPlugins': inherited(cwd, home), 'localRules': [str(p) for p in local_rules(cwd, home)]}))
