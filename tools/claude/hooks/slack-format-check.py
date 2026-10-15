#!/usr/bin/env python3
"""PreToolUse hook: no hand-typed bullets in anything bound for Slack.

Slack MCP post tools (slack_reply_to_thread, slack_post_message) send plain
`text`, which Slack renders as mrkdwn: no real lists, no `**bold**`, no
headings. A typed `•` or `- ` shows up as a fake bullet whose wrapped lines
don't indent. Those calls get the full mrkdwn check.

Code that posts to Slack itself (chat.postMessage, webhooks) runs later, from
CI or launchd, where no hook can see the message. So file writes (Claude
Write/Edit/MultiEdit, Codex apply_patch) and Bash commands are checked when
they touch Slack-posting code: adding a typed bullet there is denied. Only
added bullets count, so editing a file that already has some still works.
A line with `slack-format: ok` opts out.

A JSON deny blocks the call and tells the model why.

    slack-format-check.py --selftest
"""
import json
import os
import re
import sys

FAKE_BULLET = re.compile(r"^\s*[•◦▪‣●○∙·]\s")
MD_LIST = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s")
DOUBLE_BOLD = re.compile(r"\*\*[^*\n]+\*\*")
HEADING = re.compile(r"^#{1,6}\s")
INLINE_CODE = re.compile(r"`[^`\n]*`")

FIX = (
    "Slack MCP는 text만 보내서 진짜 목록을 만들 수 없다. "
    "요유 이름으로 보낼 글이면 마크다운 파일에 써서 `slackcopy`로 클립보드에 넣고, "
    "봇으로 올려야 하면 chat.postMessage/chat.update에 rich_text_list 블록을 담아 보낸다 "
    "(memory: reference_slack_mcp_no_real_bullets)."
)


def problems(text: str) -> list[str]:
    found, fenced = [], False
    for n, raw in enumerate(text.split("\n"), 1):
        if raw.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        line = INLINE_CODE.sub("", raw)
        if FAKE_BULLET.match(line):
            found.append(f"{n}행: 손으로 친 글머리 기호(•, ◦ 등)")
        elif MD_LIST.match(line):
            found.append(f"{n}행: 마크다운 목록(-, *, 1.)은 text로 보내면 목록이 되지 않음")
        if DOUBLE_BOLD.search(line):
            found.append(f"{n}행: **굵게**는 Slack text에서 별표가 그대로 보임 (*한 개*를 쓸 것)")
        if HEADING.match(line):
            found.append(f"{n}행: # 헤더는 Slack에 없음")
        if "·" in line and not FAKE_BULLET.match(line):
            found.append(f"{n}행: 가운뎃점 · 금지, 쉼표를 쓸 것")
    return found


# Code that sends to Slack on its own. Bullets anywhere else (UI strings, docs) are not our business.
SLACK_CODE = re.compile(
    r"chat[._](?:postMessage|update|postEphemeral|scheduleMessage)|hooks\.slack\.com|slack\.com/api"
    r"|slack_sdk|@slack/|SLACK_[A-Z_]*(?:TOKEN|WEBHOOK)"
)
# A bullet that starts a line of message text: after a newline (real or a literal \n),
# or right after a string opener / concatenation, then a space.
CODE_BULLET = re.compile(r"""(?:^|\\n|["'`(\[{+,])[ \t]*[•◦▪‣][ \t]""", re.M)
OPT_OUT = "slack-format: ok"

CODE_FIX = (
    "Slack 글을 만드는 코드에 손으로 친 글머리 기호(•, ◦)를 새로 넣으려 했다. "
    "chat.postMessage의 text 안 기호는 그냥 글자라서 목록이 되지 않는다. "
    "rich_text 블록의 rich_text_list(style bullet, indent 0/1)로 만들 것. "
    "예: monaural_beat_recommend scripts/notify_slack.py의 to_blocks()는 `- ` 줄을 진짜 목록으로 바꾼다. "
    "보낸 뒤에는 conversations.history로 블록 종류를 다시 확인한다. "
    "기호를 지우는 편집이면 Edit 도구로 하고, 정말 글자 그대로 필요하면 그 줄에 `slack-format: ok`를 적는다 "
    "(memory: reference_slack_mcp_no_real_bullets)."
)


def bullets(text: str) -> int:
    return sum(len(CODE_BULLET.findall(line)) for line in text.split("\n") if OPT_OUT not in line)


def on_disk(path: str) -> str:
    try:
        if os.path.getsize(path) <= 2_000_000:
            with open(path, encoding="utf-8") as f:
                return f.read()
    except (OSError, UnicodeDecodeError):
        pass
    return ""


def added_bullets(path: str, old: str, new: str) -> int:
    """Typed bullets this change adds to Slack-posting code (0 for anything else)."""
    if path.endswith((".md", ".markdown")):
        return 0  # docs are read, not sent
    if not SLACK_CODE.search(old + new + on_disk(path)):
        return 0
    return bullets(new) - bullets(old)


def patch_changes(patch: str, cwd: str):
    """(path, removed, added) per file of a Codex apply_patch body."""
    path, removed, added = None, [], []
    for line in patch.split("\n"):
        m = re.match(r"\*\*\* (?:Add|Update|Delete) File: (.+)", line)
        if m or line.startswith("*** End Patch"):
            if path:
                yield path, "\n".join(removed), "\n".join(added)
            path, removed, added = (os.path.join(cwd, m[1].strip()) if m else None), [], []
        elif path and line.startswith("+"):
            added.append(line[1:])
        elif path and line.startswith("-"):
            removed.append(line[1:])
    if path:
        yield path, "\n".join(removed), "\n".join(added)


def code_problems(tool: str, inp: dict, cwd: str) -> list[str]:
    """Where a file write or command adds typed bullets to Slack-bound code."""
    command = inp.get("command") or ""
    if tool in ("Write", "Edit", "MultiEdit"):
        path = inp.get("file_path") or ""
        if tool == "Write":
            changes = [(path, on_disk(path), inp.get("content") or "")]
        else:
            edits = inp.get("edits") or [inp]
            changes = [(path, e.get("old_string") or "", e.get("new_string") or "") for e in edits]
    elif "*** Begin Patch" in command:  # Codex apply_patch, as its own tool or through the shell
        changes = list(patch_changes(command, cwd))
    elif command:
        changes = [("", "", command)] if SLACK_CODE.search(command) else []
    else:
        changes = []
    return [
        f"{os.path.basename(path) or 'Bash 명령'}: 글머리 기호 {n}개 추가"
        for path, old, new in changes
        if (n := added_bullets(path, old, new) if path else bullets(new)) > 0
    ]


def selftest():
    assert problems("• 항목") and problems("    ◦ 하위")
    assert problems("- item") and problems("1. step") and problems("  * x")
    assert problems("**bold**") and problems("# 제목") and problems("A·B")
    ok = (
        "*[ 제목 ]* <@U1>\n"
        "> *결론* 한 줄\n"
        ":yoyoo-wawa::yoyoo-wawa:\n"
        "*< 섹션 >*\n"
        "본문 `- not a list` 와 `a·b`\n"
        "```\n- fenced\n• fenced\n```\n"
        "-1도 아니고 2026-10-08 날짜"
    )
    assert problems(ok) == [], problems(ok)

    B, S = "\u2022", "\u25e6"  # escaped so editing this file doesn't trip the check below
    nowhere = "/nonexistent/"
    # 10/14 usage DM: TypeScript building text lines for chat.postMessage.
    ts = "await fetch('https://slack.com/api/chat.postMessage')\nlines.push(`" + B + " ${who} ${n}건`);"
    assert code_problems("Write", {"file_path": nowhere + "report.ts", "content": ts}, "")
    # 10/15 release post: YAML TEXT with literal \\n, the Slack token named in the same file.
    yml = 'SLACK_BOT_TOKEN: x\nTEXT="*[ a ]*\\n' + B + ' 주간\\n    ' + S + ' 기준"'
    assert code_problems("Edit", {"file_path": nowhere + "train.yml", "old_string": "", "new_string": yml}, "")
    slack_line = 'msg = "' + B + ' {}"  # chat.postMessage'
    for tool, inp in [
        ("Edit", {"file_path": nowhere + "a.py", "old_string": slack_line, "new_string": slack_line + " x"}),  # same count
        ("Edit", {"file_path": nowhere + "a.py", "old_string": slack_line, "new_string": 'msg = "{}"'}),  # removes one
        ("Write", {"file_path": nowhere + "ui.ts", "content": "label = '" + B + " item'"}),  # not Slack code
        ("Write", {"file_path": nowhere + "README.md", "content": slack_line}),  # docs
        ("Write", {"file_path": nowhere + "a.py", "content": slack_line + "  # slack-format: ok"}),
        ("Write", {"file_path": nowhere + "a.py", "content": 'msg = "A ' + B + ' B"  # chat.postMessage'}),  # mid-line
        ("Bash", {"command": 'grep -c "[' + B + S + ']" notify.py  # SLACK_BOT_TOKEN'}),
        ("Bash", {"command": "echo '" + B + " not slack'"}),
    ]:
        assert code_problems(tool, inp, "") == [], (tool, inp)
    curl = "curl https://slack.com/api/chat.postMessage -d '{\"text\":\"" + B + " a\"}'"
    assert code_problems("Bash", {"command": curl}, "")
    patch = "*** Begin Patch\n*** Add File: n.py\n+import slack_sdk\n+t = \"" + B + " a\"\n*** End Patch"
    assert code_problems("apply_patch", {"command": patch}, nowhere)
    same = "*** Begin Patch\n*** Update File: n.py\n@@\n-" + slack_line + "\n+" + slack_line + " x\n*** End Patch"
    assert code_problems("apply_patch", {"command": same}, nowhere) == []
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
        sys.exit()
    payload = json.load(sys.stdin)
    tool = payload.get("tool_name") or ""
    inp = payload.get("tool_input") or {}
    if "slack" in tool.lower() and "text" in inp:  # the Slack MCP post tools
        found, fix = problems(inp.get("text") or ""), FIX
    else:
        found, fix = code_problems(tool, inp, payload.get("cwd") or os.getcwd()), CODE_FIX
    if found:  # JSON deny: Claude Code and Codex both read it
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "Slack 형식 검사에 걸렸습니다.\n- " + "\n- ".join(found) + "\n" + fix,
        }}, ensure_ascii=False))
