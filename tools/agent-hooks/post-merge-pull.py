#!/usr/bin/env python3
"""Claude PostToolUse: refresh live personal checkouts after a direct PR merge.

Only simple gh commands (optionally prefixed by cd ... &&) are accepted.
Never evaluate shell text, stash edits, switch branches, or retry a failed pull.
"""
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys

LIVE = {"Yoowatney/dotfiles": ".dotfiles",
        "Yoowatney/munice-claude": "work/munice/munice-claude"}


def git(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args],
                                   text=True, stderr=subprocess.PIPE, timeout=45).strip()


def repository(value):
    value = re.sub(r"^(?:https://github.com/|git@github.com:)", "", value)
    return value.removesuffix(".git").rstrip("/")


def target(data):
    if data.get("hook_event_name") != "PostToolUse" or data.get("tool_name") != "Bash":
        return None
    response = data.get("tool_response", {})
    if not isinstance(response, dict) or any(response.get(k) for k in
            ("exit_code", "exitCode", "interrupted", "error", "backgroundTaskId")):
        return None
    tool = data.get("tool_input", {})
    if tool.get("run_in_background"):
        return None
    lexer = shlex.shlex(tool.get("command", "").strip(), posix=True, punctuation_chars=";&|()<>\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    words = list(lexer)
    cwd = Path(data.get("cwd", "."))
    if len(words) > 3 and words[0] == "cd" and words[2] == "&&":
        cwd = cwd / Path(words[1]).expanduser()
        words = words[3:]
    if any(w in (";", "&&", "||", "|", "&", "(", ")", ">", "<", "\n") for w in words):
        return None
    if words[:3] != ["gh", "pr", "merge"] or any(
            w.split("=")[0] in ("--auto", "--disable-auto") for w in words):
        return None
    repo = None
    for index, word in enumerate(words[3:], 3):
        if word in ("-R", "--repo") and index + 1 < len(words):
            repo = words[index + 1]
        elif word.startswith("--repo="):
            repo = word.split("=", 1)[1]
        elif word.startswith("https://github.com/") and "/pull/" in word:
            repo = word.split("/pull/", 1)[0]
    return repository(repo or git(cwd, "config", "--get", "remote.origin.url"))


def warning(message):
    text = "post-merge-pull warning: " + message
    print(json.dumps(dict(systemMessage=text, hookSpecificOutput=dict(
        hookEventName="PostToolUse", additionalContext=text))))


def main():
    data = json.load(sys.stdin)
    repo = target(data)
    if repo not in LIVE:
        return
    live = Path.home() / LIVE[repo]
    if git(live, "symbolic-ref", "--short", "HEAD") != "main":
        warning(repo + ": live checkout is not on main; skipped")
        return
    if repository(git(live, "config", "--get", "remote.origin.url")) != repo:
        warning(repo + ": origin does not match; skipped")
        return
    # Explicitly choose origin/main rather than trusting a changed upstream.
    git(live, "pull", "--ff-only", "origin", "main")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError):
        # Do not echo Git stderr: remote URLs can contain credentials.
        warning("could not refresh the live checkout; HEAD and local files were not reset")
