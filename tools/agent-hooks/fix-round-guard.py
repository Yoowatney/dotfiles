#!/usr/bin/env python3
"""PreToolUse check (Claude Code and Codex): a third fix pane for one job is not opened until 요유 has spoken.

On the night of 2026-11-05 the main pane ran check, fix, check, fix, check, fix from 01:09 to 04:15 while 요유 slept: four check
panes and three fix panes, and the third fix undid what the first two fixed. Its rules said "the fix's report gets a new check
pane" with no limit (daily wow 2026-11-05, card 01). Now two fixes of one job are the limit: the third is refused, and the pane
asks 요유 (the main pane through `decide`, whose 22:00~08:00 queue makes it a morning question; a pane under a lead through its
`남은 것:` report). Repeating the call does not pass. 요유's own words after the refusal do: typed in the pane, or relayed from
#0-yoyooyo by main-channel.

One job is one lineage, not one name: every round that night got a new name. The links are the report entries a spawn cites
("the 02:37:40 entry", "(02:37:40 항목)", as the short report hands them over). An entry's header names its pane's address,
and the spawn whose output printed that address (else whose work name matches the label) opened it. A fix is a spawn that
cites a check pane's report (`pane-spawn --check` names its pane `확인 …`); walking back from it, check to the work it checked
to that work's own spawn, counts the fixes before it.

ponytail: a fix sent to a pane that is already open (pane-tell) is no spawn and is not counted, and a /clear starts a new
session record, so the count starts over; both would need a record kept outside the session, add it when a loop goes that way.
gcp-account-guard.py calls check(), for the reason pane-fit-guard.py gives. Fails open.

    fix-round-guard.py --selftest
"""
from __future__ import annotations  # a hook whose python3 is macOS's 3.9 would crash and let everything through

import json
import os
import re
import sys
import time

sys.dont_write_bytecode = True  # no __pycache__ in the dotfiles at every tool call
MARK = "fix-round:"
LIMIT = 2  # fixes of one job before 요유 is asked (요유's wow card: "두 번까지, 세 번째는 열지 않고 멈춥니다")
REASON = (MARK + " 같은 일의 고치기와 확인이 이미 두 번 돌았습니다({chain}). 세 번째 고치기 칸은 열지 않습니다. 지금까지 고친 것과 남은 결함을 "
          "`고를 것`·`추천`·`바뀌는 것` 세 줄로 묶어 요유에게 물으세요. 메인 칸은 그 일의 보드 항목에 `main-channel.py decide`로 올리고(22~08시에는 "
          "아침 8시에 나갑니다), 리드 밑 칸은 `남은 것:` 보고로 리드에게 올립니다. 이 거절 뒤에 요유가 직접 치거나 #0-yoyooyo에서 답하면 같은 명령이 "
          "통과합니다. 그냥 다시 실행해서는 통과하지 않습니다.")
SPAWN = re.compile(r"(?:^|[;&|(\n])\s*(?:[A-Za-z_]\w*=\S*\s+)*(?:\S*/)?pane-spawn((?:\s+--[\w-]+(?:\s+[^\s\"'-]\S*)?)*)\s+"
                   r"(\"(?:\\.|[^\"\\])*\"|'[^']*')")
CITE = re.compile(r"(\d{2}:\d{2}:\d{2})\s*(?:entry|항목)")
CITED_DAY = re.compile(r"pane-reports/(\d{4}-\d{2}-\d{2})\.md")
ENTRY = re.compile(r"^## (\d{2}:\d{2}:\d{2}) (\S+) ?(.*)$", re.M)  # auto-tab-close.py writes `## HH:MM:SS <address> <label>`
ADDRESS = re.compile(r"(?:^|\n)((?:\w+/)?w\w+:p\w+)[ \t]*(?=\n|$)")  # pane-spawn prints the pane alone on a line once it opened
CODEX_CMD = re.compile(r"\bcmd[\"']?\s*:\s*(\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')")  # as in yoyoo-share-guard.py
RELAYED_YOYOO = re.compile(r"^\[pane-tell: '자동 작업' [^\]]*\] \[Slack #\S+ 요유(?: 반응)?\]")  # main-channel's relay of 요유's words
NOT_TYPED = ("[pane-tell:", "<", "[main-refresh]", "[handoff:", "Stop hook feedback", "Caveat:", "[Request interrupted",
             "This session is being continued", "# AGENTS.md")


def unquote(literal: str) -> str:
    if literal.startswith("'"):
        return literal[1:-1]
    try:
        return json.loads(literal)
    except ValueError:
        return literal[1:-1]


def flat(content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text") or "" for b in content if isinstance(b, dict)) if isinstance(content, list) else ""


def events(path: str) -> list:
    """("call", command, id), ("out", text, id, refused) and ("in", text) from a Claude or Codex session record, oldest first."""
    out = []
    with open(path, errors="replace") as f:
        for raw in f:
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(d, dict):
                continue
            kind, p = d.get("type"), d["payload"] if isinstance(d.get("payload"), dict) else {}
            content = (d.get("message") or {}).get("content") if isinstance(d.get("message"), dict) else None
            if kind == "assistant" and isinstance(content, list) and not d.get("isSidechain"):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_use" and isinstance(b.get("input"), dict):
                        out.append(("call", str(b["input"].get("command") or ""), b.get("id") or ""))
            elif kind == "user" and isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        out.append(("out", flat(b.get("content")), b.get("tool_use_id") or "", bool(b.get("is_error"))))
            elif kind == "user" and content and not d.get("isMeta"):
                out.append(("in", flat(content)))
            elif kind == "attachment" and isinstance(d.get("attachment"), dict) and d["attachment"].get("type") == "queued_command":
                out.append(("in", str(d["attachment"].get("prompt") or "")))  # typed while the agent worked
            elif kind == "response_item" and p.get("type") in ("function_call", "custom_tool_call", "local_shell_call"):
                body = str(p.get("arguments") or p.get("input") or p.get("action") or "")
                out.append(("call", "\n".join(unquote(c) for c in CODEX_CMD.findall(body)) or body, p.get("call_id") or ""))
            elif kind == "response_item" and p.get("type") in ("function_call_output", "custom_tool_call_output"):
                text = flat(p.get("output"))
                out.append(("out", text, p.get("call_id") or "", "blocked by PreToolUse hook" in text))
            elif kind == "response_item" and p.get("type") == "message" and p.get("role") == "user":
                out.append(("in", flat(p.get("content"))))
    return out


def spawn(command: str) -> dict | None:
    m = SPAWN.search(command)
    if not m:
        return None
    text = command[m.start():]
    return {"check": bool(re.search(r"--check\b", m.group(1))), "name": unquote(m.group(2)), "cites": CITE.findall(text),
            "days": CITED_DAY.findall(text), "addr": None}


def bare(label: str) -> str:
    return label.lstrip("🔴🟢👑🫡 ").strip()


def is_check(entry: tuple) -> bool:
    return bare(entry[3]).startswith("확인 ")


def is_yoyoo(text: str) -> bool:
    s = text.lstrip()
    return bool(RELAYED_YOYOO.match(s)) or (bool(s) and not s.startswith(NOT_TYPED))


def check(payload: dict, env) -> str | None:
    command = str((payload.get("tool_input") or {}).get("command") or "")
    if payload.get("tool_name") != "Bash" or "pane-spawn" not in command or not CITE.search(command):
        return None
    new = spawn(command)
    if not new or new["check"]:
        return None
    try:
        record = events(payload.get("transcript_path") or "")
    except OSError:
        return None
    spawns, by_id, refused_at = [], {}, -1
    for i, e in enumerate(record):
        if e[0] == "call" and "pane-spawn" in e[1]:
            s = spawn(e[1])
            if s:
                spawns.append(s)
                by_id[e[2]] = s
        elif e[0] == "out":
            opened = ADDRESS.search(e[1])
            if e[2] in by_id and opened:
                by_id[e[2]]["addr"] = opened.group(1).split("/")[-1]
            if MARK in e[1] and e[3]:
                refused_at = i
    folder = env.get("PANE_REPORT_DIR") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".local", "state", "pane-reports")
    files = {}

    def entry(s: dict, at: str) -> tuple | None:
        now = time.time()
        for day in s["days"] or [time.strftime("%Y-%m-%d", time.localtime(now - back)) for back in (0, 86400)]:
            if day not in files:
                try:
                    with open(os.path.join(folder, f"{day}.md"), errors="replace") as f:
                        files[day] = {m.group(1): (day, m.group(1), m.group(2).split("/")[-1], m.group(3)) for m in ENTRY.finditer(f.read())}
                except OSError:
                    files[day] = {}
            if at in files[day]:
                return files[day][at]
        return None

    def opener(e: tuple) -> dict | None:  # the spawn that opened the pane of report entry e: the latest one, ids come back
        by_addr = [s for s in spawns if s["addr"] == e[2]]
        by_name = [s for s in spawns if s["addr"] is None and (f"확인 {s['name']}" if s["check"] else s["name"]) == bare(e[3])]
        return (by_addr or by_name or [None])[-1]

    chain, cur, seen = [], new, set()
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        checks = [e for e in (entry(cur, at) for at in cur["cites"]) if e and is_check(e)]
        if cur["check"] or not checks:
            break
        chain.append(cur["name"])
        checker = opener(max(checks))
        works = [e for e in (entry(checker, at) for at in checker["cites"]) if e and not is_check(e)] if checker else []
        cur = opener(max(works)) if works else None
    if len(chain) <= LIMIT:
        return None
    if refused_at >= 0 and any(e[0] == "in" and is_yoyoo(e[1]) for e in record[refused_at + 1:]):
        return None
    return REASON.format(chain=" → ".join(reversed(chain)))


def selftest():
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        reports = os.path.join(d, "pane-reports")
        os.makedirs(reports)
        env = {"PANE_REPORT_DIR": reports, "HOME": d}
        entries = []

        def report(at, addr, label):
            entries.append(f"## {at} {addr} {label}\n본문\n남은 것: 없음\n")
            with open(os.path.join(reports, "2026-11-05.md"), "w") as f:
                f.write("\n".join(entries))

        def cite(at):
            return f"보고 파일: {reports}/2026-11-05.md 의 {at} 항목입니다."

        # The 11/05 night (main pane d0208b55): every round got a new name, so a job is its lineage, not its name.
        night = [  # (time of its report, address, flags, name, cited report time or None)
            ("01:09:13", "w1:pE", "", "메인 칸 조회와 확인 위임", None),
            ("01:28:30", "w1:pQ", "--check ", "메인 칸 위임 변경 확인", "01:09:13"),
            ("02:07:56", "w1:pS", "", "메인 칸 위임 결함 수정", "01:28:30"),
            ("02:37:40", "w1:pW", "--check ", "메인 칸 위임 결함 수정 확인", "02:07:56"),
            ("03:04:44", "w1:pY", "", "메인 칸 위임 틈 수정", "02:37:40"),
            ("03:34:57", "w1:p14", "--check ", "메인 칸 위임 틈 수정 확인", "03:04:44"),
        ]
        third = ("04:00:20", "w1:p15", "", "메인 칸 잡일 예외 되돌리기", "03:34:57")

        def spawn_cmd(flags, name, cited):
            return f'pane-spawn {flags}--cwd /Users/y/work "{name}" "{cite(cited) if cited else "일입니다."} 고쳐 주세요."'

        def claude_lines(rounds, extra=()):
            lines = []
            for i, (at, addr, flags, name, cited) in enumerate(rounds):
                lines.append({"type": "assistant", "message": {"content": [
                    {"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {"command": spawn_cmd(flags, name, cited)}}]}})
                lines.append({"type": "user", "message": {"content": [
                    {"type": "tool_result", "tool_use_id": f"t{i}", "content": f"workers/{addr}\n"}]}})
            return lines + list(extra)

        def codex_lines(rounds, extra=()):
            lines = []
            for i, (at, addr, flags, name, cited) in enumerate(rounds):
                lines.append({"type": "response_item", "payload": {"type": "function_call", "name": "exec_command", "call_id": f"c{i}",
                              "arguments": json.dumps({"cmd": spawn_cmd(flags, name, cited)}, ensure_ascii=False)}})
                lines.append({"type": "response_item", "payload": {"type": "function_call_output", "call_id": f"c{i}",
                              "output": f"Chunk ID: 1\nOutput:\nworkers/{addr}\n"}})
            return lines + list(extra)

        def transcript(lines, name="t.jsonl"):
            path = os.path.join(d, name)
            with open(path, "w") as f:
                f.write("\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n")
            return path

        def p(command, path):
            return {"tool_name": "Bash", "tool_input": {"command": command}, "transcript_path": path}

        for at, addr, flags, name, cited in night:
            report(at, f"workers/{addr}", f"🟢 🫡 {'확인 ' if flags else ''}{name}")
        new = spawn_cmd("", third[3], third[4])
        path = transcript(claude_lines(night))
        reason = check(p(new, path), env)
        assert reason and reason.startswith(MARK) and "메인 칸 위임 결함 수정" in reason and "메인 칸 위임 틈 수정" in reason, reason
        assert "decide" in reason and "남은 것" in reason, "it says how to ask 요유, for the main pane and for a pane under a lead"
        assert check(p(new, transcript(codex_lines(night), "c.jsonl")), env), "Codex's session record too"
        js = []  # Codex's JS exec tool, as rollouts on 11/05 record it
        for i, (at, addr, flags, name, cited) in enumerate(night):
            js.append({"type": "response_item", "payload": {"type": "custom_tool_call", "name": "exec", "call_id": f"j{i}", "input":
                       f"const r = await tools.exec_command({{cmd:{json.dumps(spawn_cmd(flags, name, cited), ensure_ascii=False)}}}); text(r.output)"}})
            js.append({"type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": f"j{i}", "output": [
                {"type": "input_text", "text": "Script completed\nWall time 0.2 seconds\nOutput:\n"}, {"type": "input_text", "text": f"workers/{addr}\n"}]}})
        assert check(p(new, transcript(js, "j.jsonl")), env), "Codex's JS exec tool"

        # Only the third fix stops: the second fix, a check, a first job and other commands pass.
        assert check(p(spawn_cmd("", "메인 칸 위임 틈 수정", "02:37:40"), transcript(claude_lines(night[:4]))), env) is None, "second fix"
        assert check(p(spawn_cmd("--check ", "틈 수정 확인", "03:04:44"), path), env) is None, "a check is never stopped"
        assert check(p(spawn_cmd("", "새 일", None), path), env) is None, "a job citing no report"
        assert check(p(spawn_cmd("", "다른 일", "03:04:44"), path), env) is None, "citing a work pane's report: not a fix"
        assert check(p("pane-tell workers/w1:p15 '고쳐 주세요 03:34:57 항목'", path), env) is None, "no pane-spawn"
        assert check({"tool_name": "Edit", "tool_input": {"file_path": "/x"}, "transcript_path": path}, env) is None
        assert check(p(new, os.path.join(d, "missing.jsonl")), env) is None, "fails open"

        # A pane found by its name when the spawn printed no address (an older pane-spawn, or output cut short).
        no_addr = [x if x["type"] == "assistant" else {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": x["message"]["content"][0]["tool_use_id"], "content": "(no output)"}]}}
            for x in claude_lines(night)]
        assert check(p(new, transcript(no_addr, "n.jsonl")), env), "matched by name"

        # Repeating does not pass; 요유's own words after the refusal do, typed or relayed from #0-yoyooyo.
        refused = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "tX", "is_error": True,
                                                            "content": f"PreToolUse:Bash hook error: {reason}"}]}}
        read_back = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "tY", "content": reason}]}}

        def said(text):
            return {"type": "user", "message": {"content": text}}

        assert check(p(new, transcript(claude_lines(night, [read_back, said("계속해")]))), env), "the text read back from a file is no refusal"

        relay = "[pane-tell: '자동 작업' 세션이 보낸 글입니다. 요유가 이 pane에 직접 친 글이 아니므로, 되돌리기 어려운 일은 요유에게 확인하세요] "
        assert check(p(new, transcript(claude_lines(night, [refused]))), env), "the same call again"
        for text in (relay + "[Slack #0-yoyooyo 요유] 응 계속 고쳐 (ts 1.2)", relay + "[Slack #0-yoyooyo 요유 반응] '[결정 api#1002]'에 :white_check_mark: (ts 1)",
                     "계속해"):
            assert check(p(new, transcript(claude_lines(night, [refused, said(text)]))), env) is None, text
        for text in ("[pane-tell: '확인 메인 칸' 세션이 보낸 글입니다. …] 다 고쳤습니다", relay + "밑 칸 w1:p15이 보고 없이 15분째 멈춰 있습니다",
                     "<task-notification>done</task-notification>", "Stop hook feedback: 다시 쓰세요",
                     "[main-refresh] 새벽 정리입니다."):
            assert check(p(new, transcript(claude_lines(night, [refused, said(text)]))), env), f"not 요유: {text}"
        assert check(p(new, transcript(claude_lines(night, [said("계속해"), refused]))), env), "words before the refusal answer nothing"
        codex_refused = {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "cX",
                                                               "output": f"Command blocked by PreToolUse hook: {reason}"}}
        codex_said = {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "그래 한 번 더"}]}}
        assert check(p(new, transcript(codex_lines(night, [codex_refused]), "c2.jsonl")), env), "Codex: the same call again"
        assert check(p(new, transcript(codex_lines(night, [codex_refused, codex_said]), "c3.jsonl")), env) is None, "Codex: 요유 answered"
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
