#!/usr/bin/env python3
"""PreToolUse hook for the Slack MCP post tools (slack_reply_to_thread, slack_post_message).

Those tools send plain `text`, which Slack renders as mrkdwn: no real lists, no
`**bold**`, no headings. A typed `•` or `- ` shows up as a fake bullet whose
wrapped lines don't indent. A JSON deny blocks the call and tells the model why.

    slack-format-check.py --selftest
"""
import json
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
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
        sys.exit()
    payload = json.load(sys.stdin)
    text = (payload.get("tool_input") or {}).get("text") or ""
    found = problems(text)
    if found:  # JSON deny: Claude Code and Codex both read it
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "Slack 형식 검사에 걸렸습니다.\n- " + "\n- ".join(found) + "\n" + FIX,
        }}, ensure_ascii=False))
