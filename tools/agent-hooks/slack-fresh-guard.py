#!/usr/bin/env python3
"""Claude Code and Codex: look at a Slack thread again right before writing about it.

On 10/29 a session read a thread at 17:16, a reply landed at 17:21, and at 17:24 it told 요유 that nobody had answered, while the
work the reply announced was already done (api#975). That was a report to 요유, not a Slack post, so both places are checked:
  - check(): a post into a thread this session did not read in the last 3 minutes is refused once. Repeating the call passes, so a
    thread the session cannot read (a DM) never stops it. slack-format-check.py calls it: that hook is registered in both tools
    on the Slack MCP posts and Bash, so this needs no new entry and no new Codex trust.
  - report_reason(): a report saying nobody answered, when the newest thread read of the session is older than that, is asked once
    through yoyoo-share-guard.py, which runs at Stop in both tools.
Over 14 days 43 of 61 thread posts came more than 3 minutes after the read, about 3 a day, each costing one read. The limit is
SLACK_FRESH_SECONDS. It reads the session's own record, so a reader that the record does not show (a script file, a ts in a
variable) is not seen; such a post is asked once or passes, and the words of a report are matched, not understood. Fails open.

    slack-fresh-guard.py --selftest
"""
from __future__ import annotations  # a hook whose python3 is macOS's 3.9 would crash and let everything through

import datetime
import json
import os
import re
import sys
import time

sys.dont_write_bytecode = True  # no __pycache__ in the dotfiles at every tool call
FRESH = 180
READER = "/Users/yoyoo/work/munice/.agents/skills/agent-board/watch/slack-thread.sh"
READS = re.compile(r"slack-thread\.sh|conversations\.replies|slack_get_thread_replies")  # slack-thread-wait.sh only waits
WRITES = re.compile(r"chat\.postMessage|files\.completeUploadExternal")
REPLY_SCRIPT = re.compile(r"(?:^|[\s/;&|])reply\.sh\b")
TS = re.compile(r"(?<![\d.])\d{10}\.\d{6}(?![\d.])")
THREAD_TS = re.compile(r"thread_ts\W{0,8}(\d{10}\.\d{6})(?![\d.])")
# 댓글 only with 아무도: alone it was mostly GitHub comments and test tables in the 14-day replay.
NO_ANSWER = re.compile(r"아무도[^\n.,]{0,12}(?:답|회신|댓글)"
                       r"|(?<![정해])(?:답|답글|답변|회신)(?:이|은|는|도|가)?[ \t]*(?:아직[ \t]*)?"
                       r"(?:없(?!이|으면|을|다면|어도|는지|는데)|안[ \t]*(?:왔|달렸|옴)|오지[ \t]*않|달리지[ \t]*않)"
                       r"|(?<![정해대])답하지[ \t]*(?:않았|않음)")


def calls(path: str):
    """(epoch seconds, text) of every tool call in a Claude or Codex session record, oldest first."""
    with open(path, encoding="utf-8", errors="replace") as f:
        for raw in f:
            if '"tool_use"' not in raw and "_call" not in raw:
                continue
            try:
                entry = json.loads(raw)
                at = datetime.datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00")).timestamp()
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
            payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
            if payload.get("type") in ("function_call", "custom_tool_call"):
                yield at, f"{payload.get('name')} {payload.get('arguments') or payload.get('input') or ''}"
            elif entry.get("type") == "assistant" and not entry.get("isSidechain"):
                for block in (entry.get("message") or {}).get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        yield at, f"{block.get('name')} {json.dumps(block.get('input'), ensure_ascii=False)}"


def last_reads(path: str) -> dict:
    """{thread ts: epoch seconds of its newest read}."""
    reads = {}
    for at, text in calls(path):
        if READS.search(text):
            reads.update((ts, at) for ts in TS.findall(text))
    return reads


def limit(env: dict) -> int:
    return int(env.get("SLACK_FRESH_SECONDS") or FRESH)


def minutes(seconds: float) -> int:
    return max(1, round(seconds / 60))


def post_thread(payload: dict, env: dict) -> str | None:
    """The thread this call posts into, or None. Must stay cheap: every Bash call lands here."""
    tool, inp = str(payload.get("tool_name") or ""), payload.get("tool_input") or {}
    if tool.endswith("slack_reply_to_thread"):
        return str(inp.get("thread_ts") or "") or None
    command = str(inp.get("command") or "") if tool == "Bash" else ""
    if READS.search(command):
        return None  # it reads in the same call
    if REPLY_SCRIPT.search(command) and env.get("MUNICE_AMBIENT_THREAD"):
        return env["MUNICE_AMBIENT_THREAD"].rsplit("/", 1)[-1]
    found = THREAD_TS.search(command) if WRITES.search(command) else None
    return found[1] if found else None


def check(payload: dict, env: dict) -> str | None:
    """The reason to refuse this post once, or None."""
    thread = post_thread(payload, env)
    session = re.sub(r"[^\w-]", "_", str(payload.get("session_id") or ""))
    if not thread or not session:
        return None
    read = last_reads(str(payload.get("transcript_path") or "")).get(thread)
    age = None if read is None else time.time() - read
    if age is not None and age <= limit(env):
        return None
    folder = env.get("SLACK_FRESH_DIR") or os.path.expanduser("~/.local/state/slack-fresh")
    os.makedirs(folder, exist_ok=True)
    mark = os.path.join(folder, f"{session}-{thread}-{int(read or 0)}")
    if os.path.exists(mark):
        return None
    open(mark, "w").close()
    when = "이 세션에서 읽은 기록이 없습니다" if age is None else f"{minutes(age)}분 전에 읽었습니다"
    return (f"slack-fresh: 이 스레드({thread})는 {when}. 그사이에 달린 답글을 놓친 채 쓰면 안 되니, `bash {READER} <채널 ID> {thread}`로 "
            "최신 글을 다시 읽고, 그에 맞게 글을 고친 뒤 보내세요. 이 방법으로 읽을 수 없는 스레드(DM 등)면 같은 호출을 그대로 다시 실행하세요.")


def report_reason(payload: dict, text: str, env: dict) -> str | None:
    """The question for a report that says nobody answered on an old read, or None."""
    if not NO_ANSWER.search(text):
        return None
    reads = last_reads(str(payload.get("transcript_path") or ""))
    now = time.time()
    if not reads or now - max(reads.values()) <= limit(env):
        return None
    stale = sorted(reads, key=lambda ts: reads[ts], reverse=True)[:3]
    listed = ", ".join(f"{ts}({minutes(now - reads[ts])}분 전)" for ts in stale)
    return (f"slack-fresh: 보고에 답이 없다는 말이 있는데, 이 세션이 스레드를 마지막으로 읽은 지 오래됐습니다: {listed}. "
            f"`bash {READER} <채널 ID> <스레드 ts>`로 다시 읽고, 달라진 것이 있으면 보고를 고치세요. 그 말이 스레드 이야기가 아니면 그대로 끝내세요.")


def selftest():
    import json
    import subprocess
    import tempfile

    now, thread, other = time.time(), "1793260206.931999", "1793263174.168189"

    def at(minutes_ago):
        return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(now - minutes_ago * 60))

    def claude_call(minutes_ago, name, **inp):
        return {"timestamp": at(minutes_ago), "type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t", "name": name, "input": inp}]}}

    def codex_exec(minutes_ago, cmd):  # how Codex records a command: JavaScript in a custom tool call
        return {"timestamp": at(minutes_ago), "type": "response_item", "payload": {
            "type": "custom_tool_call", "name": "exec", "input": "await tools.exec_command({cmd:" + json.dumps(cmd) + "})"}}

    def codex_fn(minutes_ago, cmd):
        return {"timestamp": at(minutes_ago), "type": "response_item", "payload": {
            "type": "function_call", "name": "exec_command", "arguments": json.dumps({"cmd": cmd})}}

    read = f"bash /x/watch/slack-thread.sh C01TESTCHAN {thread} 0.0"
    curl = f'curl -s "https://slack.com/api/conversations.replies?channel=C01TESTCHAN&ts={thread}&limit=200"'
    post = f"""python3 - <<'EOF'
json.dumps({{"channel": "C01TESTCHAN", "thread_ts": "{thread}", "text": "t"}})  # https://slack.com/api/chat.postMessage
EOF"""

    with tempfile.TemporaryDirectory() as d:
        env = {"SLACK_FRESH_DIR": d}

        def log(*entries):
            f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", dir=d, delete=False)
            f.write("not json{\n" + "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries))
            f.close()
            return f.name

        def bash(command, path, session="s1", tool="Bash"):
            return {"session_id": session, "tool_name": tool, "tool_input": {"command": command}, "transcript_path": path}

        def mcp(path, ts=thread, session="s1"):
            return {"session_id": session, "tool_name": "mcp__slack__slack_reply_to_thread", "transcript_path": path,
                    "tool_input": {"channel_id": "C01TESTCHAN", "thread_ts": ts, "text": "t"}}

        # Every way a session reads a thread, in both tools' records.
        for entry in (claude_call(8, "Bash", command=read), claude_call(8, "Bash", command=curl),
                      claude_call(8, "mcp__slack__slack_get_thread_replies", channel_id="C01TESTCHAN", thread_ts=thread),
                      codex_exec(8, read), codex_fn(8, curl)):
            got = last_reads(log(entry))
            assert abs(got.get(thread, 0) - (now - 480)) < 3, f"read not found: {entry}"
        assert last_reads(log(claude_call(8, "Bash", command=f"bash /x/watch/slack-thread-wait.sh C01TESTCHAN {thread}"))) == {}, \
            "a background wait is not a read"
        assert last_reads(log(claude_call(8, "Bash", command=f"gh issue comment 1 --body {thread}"))) == {}, "nor is naming the ts"
        result = {"timestamp": at(8), "type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t", "content": read}]}}
        assert last_reads(log(result)) == {}, "a command printed in a tool result is no read"
        assert last_reads(log(claude_call(8, "Bash", command=read), claude_call(1, "Bash", command=read)))[thread] > now - 90, "the newest read counts"

        # Before a post (a Slack MCP reply, chat.postMessage in a shell command, ambient's reply.sh).
        stale, fresh = log(claude_call(8, "Bash", command=read)), log(claude_call(1, "Bash", command=read))
        assert "8분" in (check(mcp(stale), env) or ""), "read 8 minutes ago: re-read first"
        assert check(mcp(stale), env) is None, "the repeat passes: a thread that cannot be read must not wedge the session"
        assert "8분" in (check(mcp(stale, session="s2"), env) or ""), "another session is asked again"
        assert check(mcp(fresh, session="s3"), env) is None, "read a minute ago"
        assert check(mcp(fresh, other, session="s3"), env), "another thread was never read"
        again = log(claude_call(40, "Bash", command=read), claude_call(8, "Bash", command=read))
        assert check(mcp(again, session="s4"), env), "a new stale read asks anew"
        assert check(bash(post, stale, "s5"), env), "chat.postMessage with thread_ts"
        assert check(bash(post, fresh, "s6"), env) is None
        assert check(bash(post.replace(thread, "${TS}"), stale, "s7"), env) is None, "ponytail: a ts in a variable is not seen"
        assert check(bash(f"{curl}; {post}", stale, "s8"), env) is None, "the same command reads first"
        assert check(bash("git commit -m 'post with chat.postMessage and thread_ts'", stale, "s9"), env) is None, "no thread named"
        assert check(bash("curl https://slack.com/api/chat.postMessage -d channel=C1 -d text=t", stale, "s10"), env) is None, "a new post, no thread"
        ambient = {**env, "MUNICE_AMBIENT_THREAD": f"C01TESTCHAN/{thread}"}
        assert check(bash("bash /x/ambient/reply.sh /tmp/reply.txt", stale, "s11"), ambient), "reply.sh names no ts"
        assert check(bash("bash /x/ambient/reply.sh /tmp/reply.txt", fresh, "s12"), ambient) is None
        assert check(bash("bash /x/ambient/reply.sh /tmp/reply.txt", stale, "s13"), env) is None, "no thread outside ambient"
        codex = log(codex_exec(8, read))
        assert check(bash(post, codex, "s14"), env), "a Codex session's old read"
        assert check(bash(post, log(codex_exec(1, read)), "s15"), env) is None, "a Codex session's new read"
        assert check({"session_id": "s16", "tool_name": "Bash"}, env) is None, "no input"
        assert check({**mcp(stale), "session_id": ""}, env) is None, "no session id: nothing to key on"
        assert check(mcp(stale, session="s17"), {**env, "SLACK_FRESH_SECONDS": "3600"}) is None, "the limit can be changed"

        # Before a report: the 10/29 reply said nobody had answered, 8 minutes after the read. A reply had come 3 minutes in.
        said = "유요 몫: 스레드의 bastion 질문에 답하기(아직 아무도 답하지 않음) · 세션: 닫아도 됨"
        stop = {"session_id": "s1", "transcript_path": stale}
        assert "8분" in (report_reason(stop, said, env) or ""), "the 10/29 report comes back"
        assert report_reason({**stop, "transcript_path": fresh}, said, env) is None, "the same words a minute after the read pass"
        assert report_reason({**stop, "transcript_path": log(codex_exec(8, read))}, said, env), "a Codex session's report"
        assert report_reason({**stop, "transcript_path": log()}, said, env) is None, "no thread read in this session"
        for words in ("답글이 없습니다.", "회신이 아직 없습니다.", "답이 안 왔습니다.", "답글이 달리지 않았습니다.", "아무도 회신하지 않았습니다.", "루나의 질문에 아직 답하지 않았습니다."):
            assert report_reason(stop, words, env), f"should ask: {words}"
        for words in ("답이 없으면 추천안대로 진행합니다.", "응답 없이 턴을 끝냅니다.", "테스트를 통과했습니다.", "정답이 없습니다.",
                      "200, 댓글 없음", "그런 글에는 답하지 않습니다.", "답하지 않은 날은 빼고 셉니다."):
            assert report_reason(stop, words, env) is None, f"not a claim about a thread: {words}"
        assert report_reason(stop, said, {**env, "SLACK_FRESH_SECONDS": "3600"}) is None, "the limit can be changed"

        # Through the real entry points, the way both tools call them.
        here = os.path.dirname(os.path.realpath(__file__))
        fmt = os.path.join(here, "..", "claude", "hooks", "slack-format-check.py")

        def run(script, payload, *args, **extra):
            return subprocess.run([sys.executable, "-I", script, *args], input=json.dumps(payload), capture_output=True, text=True,
                                  env={**os.environ, **env, "HERDR_BIN_PATH": "/nonexistent/herdr", **extra}, timeout=10)

        out = run(fmt, mcp(stale, session="s20"))
        decision = json.loads(out.stdout)["hookSpecificOutput"]
        assert decision["permissionDecision"] == "deny" and "8분" in decision["permissionDecisionReason"], out.stdout
        assert run(fmt, mcp(stale, session="s20")).stdout == "", "the repeat passes through the hook"
        assert run(fmt, mcp(fresh, session="s21")).stdout == "", "a fresh read passes through the hook"
        assert run(fmt, mcp("/nonexistent/x.jsonl", session="s22")).stdout == "", "a missing transcript passes"
        share = os.path.join(here, "yoyoo-share-guard.py")
        hook = {**stop, "hook_event_name": "Stop", "stop_hook_active": False, "last_assistant_message": said}
        assert "8분" in json.loads(run(share, hook).stdout)["reason"], "Codex reads decision:block"
        reason = json.loads(run(share, hook, "--claude").stdout)["hookSpecificOutput"]["additionalContext"]
        assert "8분" in reason, "Claude reads additionalContext"
        assert run(share, {**hook, "transcript_path": "/nonexistent/x.jsonl"}).stdout == "", "a missing transcript passes"
        assert run(share, {**hook, "stop_hook_active": True}).stdout == "", "second stop of a turn must pass"
    print("selftest ok")


if __name__ == "__main__" and "--selftest" in sys.argv:
    selftest()
