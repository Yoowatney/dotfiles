#!/usr/bin/env python3
"""Read redacted user requests without replaying assistant or tool output.

Usage: python3 -I ~/.claude/hooks/session-user-messages.py <transcript.jsonl>
Only transcripts under this user's runtime directories are accepted. There is
no raw mode. Redaction is conservative, not a guarantee for unlabeled secrets;
code blocks are omitted entirely, so missing detail must remain unverified.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys

REDACTED = "[REDACTED]"
LABEL = (r"(?:[\w.-]*(?:token|secret|password|passwd|api[_-]?key|private[_-]?key|credential)[\w.-]*"
         r"|authorization|비밀번호|암호|비밀|토큰|API\s*키)")
ASSIGNMENT = re.compile(r"(?i)([\"']?" + LABEL + r"[\"']?\s*[:=]\s*)"
                        r"(?:\"[^\"\n]*\"|'[^'\n]*'|[^\s,;&}\]]+)")


def redact(text: str) -> str:
    # Omit complete and unfinished fenced code, including values with no label.
    text = re.sub(r"(?ms)^\s*(`{3,}|~{3,})[^\n]*\n.*?(?:^\s*\1\s*$|\Z)", REDACTED, text)
    text = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----.*?(?:-----END [^-]*PRIVATE KEY-----|\Z)",
                  REDACTED, text, flags=re.S)
    text = re.sub(r"(?i)\b(?:Bearer|Basic)\s+[^\s\"'<>]+", REDACTED, text)
    text = ASSIGNMENT.sub(lambda m: m[1] + REDACTED, text)
    text = re.sub(r"(?i)(https?://)[^\s/@]+:[^\s/@]+@", r"\1" + REDACTED + "@", text)
    text = re.sub(r"\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]+|xox[baprs]-[A-Za-z0-9-]+)\b",
                  REDACTED, text)
    # Long opaque values are hidden even without a recognizable provider prefix.
    text = re.sub(r"(?<![\w])(?=[A-Za-z0-9_+/=-]{24,}(?![\w]))(?=[A-Za-z0-9_+/=-]*[A-Za-z])"
                  r"(?=[A-Za-z0-9_+/=-]*\d)[A-Za-z0-9_+/=-]{24,}", REDACTED, text)
    return text


def user_text(record: object) -> str | None:
    if not isinstance(record, dict):
        return None
    if record.get("type") == "response_item":
        message = record.get("payload")
        if not isinstance(message, dict) or message.get("type") != "message" or message.get("role") != "user":
            return None
        metadata = message.get("internal_chat_message_metadata_passthrough") or {}
        if not isinstance(metadata, dict):
            return None
        items = metadata.get("content_item_metadata") or []
        if not isinstance(items, list):
            return None
        if any(isinstance(item, dict) and isinstance(item.get("provenance"), dict)
               and item["provenance"].get("type") == "harness" for item in items):
            return None
    elif record.get("type") == "user":
        if record.get("isMeta") or record.get("isCompactSummary"):
            return None
        origin = record.get("origin") or {}
        if not isinstance(origin, dict) or origin.get("kind", "human") != "human":
            return None
        message = record.get("message")
        if not isinstance(message, dict) or message.get("role", "user") != "user":
            return None
    else:
        return None
    content = message.get("content")
    if isinstance(content, list):
        content = "\n".join(part["text"] for part in content if isinstance(part, dict)
                            and part.get("type") in {"text", "input_text"} and isinstance(part.get("text"), str))
    if not isinstance(content, str) or not content.strip():
        return None
    if content.startswith(("# AGENTS.md instructions", "<environment_context>", "<turn_aborted>")):
        return None
    return redact(content)


def main() -> int:
    if len(sys.argv) != 2:
        print("Expected one transcript path; raw output is unavailable.", file=sys.stderr)
        return 2
    try:
        path = Path(sys.argv[1]).expanduser().resolve(strict=True)
        home = Path.home().resolve()
        roots = [home / ".codex/sessions", home / ".codex/archived_sessions", home / ".claude/projects"]
        if path.suffix != ".jsonl" or not any(root in path.parents for root in roots):
            raise ValueError("outside transcript directories")
        messages = []
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    text = user_text(json.loads(line))
                except (ValueError, TypeError):
                    continue
                if text:
                    messages.append(text)
    except (OSError, ValueError, RuntimeError):
        print("Cannot read an allowed transcript.", file=sys.stderr)
        return 2
    print(json.dumps({"messages": messages}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
