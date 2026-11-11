#!/usr/bin/env python3
"""Install/remove only the Codex handoff hook, preserving all other settings."""
import json
from pathlib import Path
import shlex
import sys

from importlib.util import spec_from_file_location, module_from_spec

HERE = Path(__file__).resolve().parent
spec = spec_from_file_location("handoff_state", HERE / "handoff-state.py")
state = module_from_spec(spec)
spec.loader.exec_module(state)


def install(remove=False):
    path = Path.home() / ".codex/hooks.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    if not isinstance(data, dict):
        raise ValueError("hooks.json must be an object")
    hooks = data.setdefault("hooks", {})
    groups = hooks.setdefault("SessionStart", [])
    if not isinstance(groups, list):
        raise ValueError("SessionStart must be a list")
    command = "bash " + shlex.quote(str(HERE / "handoff")) + " session codex"
    kept = []
    for group in groups:
        entries = group["hooks"]
        remaining = [entry for entry in entries if entry.get("command") != command]
        if remaining or not entries:
            kept.append(dict(group, hooks=remaining))
    if not remove:
        kept.append(dict(hooks=[dict(type="command", command=command, timeout=10)]))
    hooks["SessionStart"] = kept
    state.atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    try:
        if sys.argv[1:] not in ([], ["--remove"]):
            raise ValueError("usage: install-handoff.py [--remove]")
        install(bool(sys.argv[1:]))
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        print("handoff install: " + str(error), file=sys.stderr)
        sys.exit(1)
