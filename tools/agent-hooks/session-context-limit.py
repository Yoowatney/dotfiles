#!/usr/bin/env python3
"""Bound a data-only SessionStart hook, preserving its complete text privately.

Use only for board/task context, never rule files, permissions, or safety hooks.
The wrapped command receives the original stdin and its exit status is retained.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True, choices=["board"])
    parser.add_argument("--limit", type=int, default=2000)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command or args.limit < 1:
        parser.error("a command and a positive context limit are required")
    payload = sys.stdin.buffer.read()
    result = subprocess.run(command, input=payload, stdout=subprocess.PIPE)
    output = result.stdout
    try:
        value = json.loads(output)
        hook = value["hookSpecificOutput"]
        context = hook["additionalContext"]
        if hook.get("hookEventName") == "SessionStart" and isinstance(context, str) and len(context) > args.limit:
            # An immutable file prevents another session or /clear from replacing it.
            state = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
            directory = state / "agent-startup-context" / args.label
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            key = hashlib.sha256(payload).hexdigest()[:12]
            fd, path = tempfile.mkstemp(prefix=key + "-", suffix=".txt", dir=directory)
            with os.fdopen(fd, "w") as saved:
                saved.write(context)
            header = context.splitlines()[0][:400]
            hook["additionalContext"] = (header + "\nFull board context is saved at " + path
                + ". Read it before selecting or updating work. This file contains the complete original hook output.")
            output = (json.dumps(value, ensure_ascii=False) + "\n").encode()
    except (ValueError, KeyError, TypeError, OSError) as error:
        # A failed archive must never silently discard the context.
        print(f"session-context-limit: original output retained ({type(error).__name__})", file=sys.stderr)
    sys.stdout.buffer.write(output)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
