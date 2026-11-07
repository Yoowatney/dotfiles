#!/usr/bin/env python3
"""Read human prompts from Claude Code and Codex JSONL without trusting tool output.

CLI: unattended-session.py --active dispatch|ambient TRANSCRIPT
Exit 0 while unattended, 10 after takeover, 2 for an invalid invocation.
Missing or unreadable transcripts never lift a guard. This recognizes envelopes,
not the person typing: a relay typed into a pane has the same origin as a person.
"""
from __future__ import annotations

import json
import sys

SCAFFOLD = ("# AGENTS.md", "<environment_context>", "<user_instructions>", "<hook_prompt", "<user_action>")


def text_content(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(part["text"] for part in content if isinstance(part, dict)
                         and part.get("type") in ("text", "input_text") and isinstance(part.get("text"), str))
    return ""


def human_prompt(record: dict) -> str:
    if record.get("type") == "user" and (record.get("origin") or {}).get("kind") == "human":
        return text_content((record.get("message") or {}).get("content"))
    attachment = record.get("attachment")
    if (record.get("type") == "attachment" and isinstance(attachment, dict)
            and attachment.get("type") == "queued_command"
            and (attachment.get("origin") or {}).get("kind") == "human"):
        return text_content(attachment.get("prompt"))
    payload = record.get("payload")
    if record.get("type") == "response_item" and isinstance(payload, dict) and payload.get("role") == "user":
        text = text_content(payload.get("content"))
        return "" if text.lstrip().startswith(SCAFFOLD) else text
    return ""


def active(path: str, mode: str) -> bool:
    marker = "ambient-round" if mode == "ambient" else "auto-dispatch"
    last = ""
    try:
        with open(path, encoding="utf-8", errors="replace") as transcript:
            for line in transcript:
                try:
                    record = json.loads(line)
                    prompt = human_prompt(record) if isinstance(record, dict) else ""
                except (ValueError, TypeError, AttributeError):
                    continue
                if prompt.strip():
                    if mode == "dispatch" and marker not in prompt:
                        return False
                    last = prompt
    except OSError:
        return True
    return not last or marker in last


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] != "--active" or sys.argv[2] not in ("dispatch", "ambient"):
        sys.exit(2)
    # A Python crash normally exits 1. Never confuse that with a deliberate takeover.
    sys.exit(0 if active(sys.argv[3], sys.argv[2]) else 10)
