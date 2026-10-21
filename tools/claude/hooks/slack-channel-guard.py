#!/usr/bin/env python3
"""PreToolUse hook: Slack writes go to #임시작업 (and #3-release-dev) only.

요유 asked on 2026-10-20 that sessions write to Slack (posts, thread replies,
reactions) only in #임시작업 until they lift the rule. On 2026-10-21 they also
allowed #3-release-dev, for release notices they ask for (release-notify).
Reading any channel is fine. When the rule is lifted, delete this file and its entries in
~/.claude/settings.json and ~/.codex/hooks.json.
Memory: feedback_slack_writes_temp_work_channel.

Checked: Slack MCP tools that are not get/list/search/read tools, and Bash or
Monitor commands that call a Slack write API, an incoming webhook, or nightly_api's
slack-post.sh. The target channel must appear in the command as a literal ID.
A variable or a payload file is denied, because the hook cannot see it.
Not checked: wrappers that keep the channel inside (board.sh notices,
alarm-post-comment.sh in the alarm job), and scripts a session writes and then runs.

    slack-channel-guard.py --selftest
"""
import json
import os
import re
import sys

ALLOWED = "C0CAND31MA8"  # #임시작업
RELEASE = "C0536DQTREV"  # #3-release-dev, release notices 요유 asks for (2026-10-21)
WRITABLE = {ALLOWED, RELEASE}

READ_TOOL = re.compile(r"(?:^|_)(?:get|list|search|read|fetch|lookup)", re.I)
HTTP_CLIENT = re.compile(r"\b(?:curl|wget|requests|urllib|fetch|axios|httpx|xh)\b")
WRITE_API = re.compile(
    r"slack\.com/api/(?:chat\.(?:postMessage|update|delete|postEphemeral|scheduleMessage|meMessage)"
    r"|reactions\.(?:add|remove)|files\.(?:upload|completeUploadExternal)|pins\.add|bookmarks\.add|canvases\."
    r"|conversations\.(?:create|invite|kick|rename|archive|setTopic|setPurpose))"
)
WEBHOOK = re.compile(r"hooks\.slack\.com/(?:services|workflows|triggers)/")
SDK_WRITE = re.compile(r"\.(?:chat_(?:postMessage|update|delete|postEphemeral|scheduleMessage)|reactions_add|files_upload(?:_v2)?)\(")
# Slack IDs carry a digit, which keeps words like CONTAINER out
CHANNEL_ID = re.compile(r"\b[CDG](?=[A-Z0-9]*\d)[A-Z0-9]{8,}\b")
# links to other channels inside the message text are not targets
REFS = re.compile(r"https?://[^\s'\"<>|)]*/archives/[^\s'\"<>|)]*|<#[A-Z0-9]+[^>]*>")
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def runs_slack_post(command: str) -> bool:
    """nightly_api scripts/deploy/slack-post.sh <channel> as the command of a pipeline segment, not as a grep word."""
    for segment in re.split(r"[;|&\n]", command):
        words = segment.split()
        while words and ASSIGNMENT.match(words[0]):
            words.pop(0)
        if words and words[0] in ("bash", "sh", "zsh"):
            words.pop(0)
        if words and os.path.basename(words[0].strip("'\"")) == "slack-post.sh":
            return True
    return False


def problem(tool: str, inp: dict) -> str | None:
    if tool.startswith("mcp__") and "slack" in tool.lower():
        if READ_TOOL.search(tool.rsplit("__", 1)[-1]):
            return None
        channel = inp.get("channel_id") or inp.get("channel") or ""
        return None if channel in WRITABLE else f"{tool.rsplit('__', 1)[-1]}의 채널이 {channel or '(없음)'}"
    command = inp.get("command") if tool in ("Bash", "Monitor") else None
    if not isinstance(command, str):
        return None
    client = HTTP_CLIENT.search(command)
    if client and WEBHOOK.search(command):
        return "웹훅은 어느 채널로 가는지 명령에서 보이지 않음"
    if not ((client and WRITE_API.search(command)) or SDK_WRITE.search(command) or runs_slack_post(command)):
        return None
    targets = set(CHANNEL_ID.findall(REFS.sub("", command)))
    if not targets:
        return "글을 쓸 채널 ID가 명령에 없음(변수나 파일로 넘기면 확인할 수 없으니 ID를 명령에 그대로 쓸 것)"
    others = targets - WRITABLE
    return f"다른 채널: {', '.join(sorted(others))}" if others else None


def selftest() -> None:
    other = "C078C6SHC87"
    mcp_cases = [
        ("mcp__slack__slack_post_message", {"channel_id": other, "text": "x"}, True),
        ("mcp__slack__slack_post_message", {"channel_id": ALLOWED, "text": "x"}, False),
        ("mcp__slack__slack_reply_to_thread", {"channel_id": other, "thread_ts": "1.2", "text": "x"}, True),
        ("mcp__slack__slack_reply_to_thread", {"channel_id": ALLOWED, "thread_ts": "1.2", "text": "x"}, False),
        ("mcp__slack__slack_add_reaction", {"channel_id": other, "timestamp": "1.2", "reaction": "eyes"}, True),
        ("mcp__claude_ai_Slack__slack_send_message", {"channel": other, "text": "x"}, True),
        ("mcp__claude_ai_Slack__slack_create_canvas", {"title": "x"}, True),  # unknown write, no channel
        ("mcp__slack__slack_get_thread_replies", {"channel_id": other, "thread_ts": "1.2"}, False),
        ("mcp__slack__slack_get_channel_history", {"channel_id": other}, False),
        ("mcp__slack__slack_list_channels", {}, False),
        ("mcp__claude_ai_Slack__slack_read_thread", {"channel_id": other}, False),
        ("mcp__claude_ai_Slack__slack_search_public", {"query": "x"}, False),
        ("Write", {"file_path": "/tmp/x", "content": f"chat.postMessage {other}"}, False),
    ]
    api = "https://slack.com/api/chat.postMessage"
    bash_cases = [
        (f"curl -s -X POST {api} -H 'Authorization: Bearer ${{token}}' --data-urlencode channel={ALLOWED} --data-urlencode text=hi", False),
        (f"jq -n --arg c {ALLOWED} --arg t hi '{{channel:$c,text:$t}}' | curl -s -X POST {api} -d @-", False),
        # a link to another channel's message inside the text is not a target
        (f"curl -s {api} -d channel={ALLOWED} -d 'text=<https://municehq.slack.com/archives/{other}/p1791955069106609|스레드> <#{other}|임시>'", False),
        (f"curl -s {api} -d channel={other} -d text=hi", True),
        (f"curl -s {api} -d channel=$CH -d text=hi", True),  # channel not visible
        (f"curl -s https://slack.com/api/chat.update -d channel={other} -d ts=1.2 -d text=x", True),
        (f"curl -s https://slack.com/api/reactions.add -d channel={other} -d name=eyes -d timestamp=1.2", True),
        ("curl -s -X POST https://hooks.slack.com/services/T/B/x -d '{\"text\":\"hi\"}'", True),
        (f"printf '%s\\n' '*[ a ]*' | GCP_PROJECT_ID=p scripts/deploy/slack-post.sh {RELEASE} < msg.txt", False),
        (f"printf '%s\\n' '*[ a ]*' | GCP_PROJECT_ID=p scripts/deploy/slack-post.sh {other}", True),
        (f"printf '%s\\n' '*[ a ]*' | bash scripts/deploy/slack-post.sh {ALLOWED}", False),
        (f"python3 -c 'from slack_sdk import WebClient; WebClient().chat_postMessage(channel=\"{other}\", text=\"x\")'", True),
        # reads and searches stay open
        (f"curl -sfG https://slack.com/api/conversations.history -d channel={other}", False),
        (f"curl -sfG https://slack.com/api/conversations.replies -d channel={other} -d ts=1.2", False),
        (f"curl -s -X POST https://slack.com/api/conversations.join -d channel={other}", False),
        (f"bash .agents/skills/agent-board/watch/slack-thread.sh {other} 1.2", False),
        (f"grep -rn 'slack.com/api/chat.postMessage' . ; grep -n slack-post.sh SKILL.md", False),
        ("grep -rn hooks.slack.com/services terraform/", False),
        ("printf 'x' | bash board.sh report", False),  # documented gap: board.sh keeps its channel inside
        # uppercase words are not channel IDs
        (f"DEPLOYMENT_CONTAINER=GOOGLEAPIS curl -s {api} -d channel={ALLOWED} -d text=CONTAINERS", False),
    ]
    for tool, inp, deny in mcp_cases:
        assert bool(problem(tool, inp)) == deny, (tool, inp, problem(tool, inp))
    for cmd, deny in bash_cases:
        for tool in ("Bash", "Monitor"):
            assert bool(problem(tool, {"command": cmd})) == deny, (tool, cmd, problem(tool, {"command": cmd}))
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
        sys.exit()
    payload = json.load(sys.stdin)
    found = problem(payload.get("tool_name") or "", payload.get("tool_input") or {})
    if found:  # JSON deny: Claude Code and Codex both read it
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"Slack 쓰기는 #임시작업({ALLOWED})과, 요유가 시킨 릴리스 공지의 #3-release-dev({RELEASE})에만 "
                f"할 수 있습니다(요유 10/20·10/21, 요유가 풀 때까지). {found}. "
                "그 채널에 없는 사람에게 갈 글이면 요유 말투 초안을 slackcopy로 넘기세요. 읽기는 어느 채널이든 됩니다."
            ),
        }}, ensure_ascii=False))
