#!/usr/bin/env python3
"""Count, per KST day, what the main-pane trial is judged by (design §9, api#1002): 요유's own messages, the panes they answered in,
and progress questions, from this Mac's Claude and Codex session records.

    trial-count.py [--since 2026-11-05] [--until 2026-11-11] [--base 2026-10-21:2026-11-04] [--list]
    trial-count.py --selftest

It counts the baseline window again with the same method, so the two columns compare like for like. The 11/04 baseline
(`2026-11-04-manager-layer-need.html`) sorted 195 regex hits by hand into 35 progress questions and 14 "왜 내가" pushbacks
(49); its regex was not kept, so `questions` here is the regex of that study's progress_q.py, and `--list` prints the hits
for the same hand sorting. Sessions are sorted as that study's extract.py did: sub-panes (a lead's instruction), test and
headless sessions are left out, and relayed text is not 요유's. One difference: since 2026-11-05 요유 also writes to the main
pane through Slack #0-yoyooyo, relayed by main-channel; those are counted apart (`slack`, `reactions`), never as typed, and
`panes` counts only the panes 요유 typed in (design §9: the panes 요유 answered in their own herdr list). What 요유 types while
the agent works is a `queued_command` attachment, not a user line; those are counted too (about 5 a day in the baseline).

ponytail: this Mac only. The 11/04 study also read 집맥: 136 of its 3,612 rows, 58 of them 요유's lines. 37 of those (gomgom 22,
munice job tabs 10, other munice sessions 5) are in files only 집맥 has, so this count is lower by them; the other 21 were two
10/23 sessions whose file was on both Macs, which the study counted twice. No 회사맥 file went missing (all 516 the study read
were there on 11/05), and Claude keeps records 30 days, so a recount on 11/11 still reads from 10/21. A /clear splits one pane
into two sessions, as it did in the baseline.
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os
import re
import sys
import tempfile

sys.dont_write_bytecode = True
KST = dt.timezone(dt.timedelta(hours=9))
INJECT = ("[pane-tell:", "[handoff:", "ambient-round]", "[auto-dispatch]", "Stop hook feedback", "[main-refresh]",
          "This session is being continued from a previous conversation", "[Request interrupted", "Caveat:",
          "<task-notification>", "<system-reminder>", "<local-command", "<bash-", "<command-name>",
          "Here is a note offered by a side agent")
SUBPANE = "이 칸은 리드 밑 칸입니다"
TEST = re.compile(r"도구를 쓰지 말고|한 단어만 답하|Without using tools|^\s*Call mcp__|훅 테스트|이 시험|시험입니다|"
                  r"^\s*(test|테스트)\b|그대로 (출력|답)하|This is a hook test", re.I)
SLACK = re.compile(r"^\[pane-tell: '자동 작업' [^\]]*\] \[Slack #\S+ 요유( 반응)?\] ?", re.S)
BATCH = re.compile(r"\n\n(?=\[pane-tell: ')")  # as fix-round-guard.py
QUESTION = [re.compile(p) for p in (  # progress_q.py, 2026-11-04
    r"남은\s*(거|것|게|일)|남았(어|니|냐|나)",
    r"내가\s*(할|해야\s*할|해야할)\s*(거|것|게|일)|내\s*몫|내가\s*뭘\s*(해|결정)|요유\s*몫",
    r"(꺼|끄|닫|종료해)도\s*(돼|되|됨|될)",
    r"어디까지",
    r"얼마나\s*(걸|남|더)",
    r"(다\s*(됐|되었|된|끝)|끝났(어|니|냐|나)|완료(됐|된|했)(어|니|냐|나)?\s*\?)",
    r"진행\s*(상황|상태|중이야|됐)|어떻게\s*(됐|되고)|어케\s*됐|됐(어|니|냐)\s*\?")]


def sessions(home: str, since_ts: float):
    """(path, entrypoint or originator, [(utc iso, text)]) for each record modified since since_ts: user lines only."""
    seen = set()
    for path in glob.glob(f"{home}/.claude/projects/*/*.jsonl"):
        if os.path.getmtime(path) < since_ts:
            continue
        rows, ep = [], ""
        for line in open(path, errors="replace"):
            if '"type":"user"' not in line and '"type": "user"' not in line and '"queued_command"' not in line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            ep = ep or r.get("entrypoint") or ""
            if r.get("uuid") in seen or r.get("isMeta") or r.get("isSidechain"):
                continue
            seen.add(r.get("uuid"))
            a = r.get("attachment") if isinstance(r.get("attachment"), dict) else {}
            if r.get("type") == "attachment" and a.get("type") == "queued_command":
                # typed while the agent worked; the rest is a background task, another session or /goal
                if a.get("commandMode", "prompt") != "prompt" or (a.get("origin") or {}).get("kind", "human") != "human":
                    continue
                c = a.get("prompt")
            elif r.get("type") == "user":  # not an agent's line that quotes one
                c = (r.get("message") or {}).get("content")
            else:
                continue
            if isinstance(c, list):
                c = "" if any(isinstance(x, dict) and x.get("type") == "tool_result" for x in c) else \
                    "\n".join(x.get("text") or "" for x in c if isinstance(x, dict) and x.get("type") == "text")
            if isinstance(c, str) and c:
                rows.append((r.get("timestamp") or "", c))
        yield path, "headless" if ep.startswith("sdk") else "", rows
    for path in glob.glob(f"{home}/.codex/sessions/**/*.jsonl", recursive=True):
        if os.path.getmtime(path) < since_ts:
            continue
        rows, hint = [], ""
        for line in open(path, errors="replace"):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            p = r.get("payload") if isinstance(r.get("payload"), dict) else {}
            if r.get("type") == "session_meta" and (p.get("originator") != "codex-tui" or p.get("source") not in ("cli", "vscode")):
                hint = "headless"
            elif r.get("type") == "event_msg" and p.get("type") == "item_completed":
                it = p.get("item") or {}
                text = "\n".join(x.get("text") or "" for x in it.get("content") or [] if isinstance(x, dict))
                if it.get("type") == "UserMessage" and text:
                    rows.append((r.get("timestamp") or "", text))
        yield path, hint, rows


def kind(path: str, hint: str, rows: list) -> str:
    first = "\n".join(t for _, t in rows[:3])
    if hint == "headless":
        return "headless"
    if "/private/tmp/" in path or "-private-" in path or (rows and TEST.search(rows[0][1])):
        return "test"
    return "subpane" if SUBPANE in first else "counted"


def count(home: str, since: str, until: str, listing: list | None = None) -> dict:
    """{day: {typed, slack, reactions, panes, questions}} for KST days since..until, both included."""
    start = dt.datetime.fromisoformat(since).replace(tzinfo=KST)
    end = dt.datetime.fromisoformat(until).replace(tzinfo=KST) + dt.timedelta(days=1)
    days, panes = {}, {}
    for path, hint, rows in sessions(home, start.timestamp() - 86400):
        if kind(path, hint, rows) != "counted":
            continue
        # pane-tell sends what it held for the main pane as one prompt, each message with its own mark (api#1051)
        for ts, text in ((ts, part) for ts, whole in rows for part in BATCH.split(whole)):
            try:
                at = dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(KST)
            except ValueError:
                continue
            if not start <= at < end:
                continue
            m = SLACK.match(text)
            if m:
                body, what = text[m.end():], "reactions" if m.group(1) else "slack"
            elif any(p in text.lstrip()[:400] for p in INJECT):  # anywhere in the head, as extract.py looked
                continue
            else:
                body, what = text, "typed"
            day = at.strftime("%Y-%m-%d")
            row = days.setdefault(day, {"typed": 0, "slack": 0, "reactions": 0, "panes": 0, "questions": 0})
            row[what] += 1
            if what == "typed":
                panes.setdefault(day, set()).add(path)
            if what != "reactions" and len(body) <= 300 and "<pasted_content" not in body and any(q.search(body) for q in QUESTION):
                row["questions"] += 1
                if listing is not None:
                    listing.append(f"{at:%m-%d %H:%M} {what} {os.path.basename(path)[:8]} {body[:160]!r}")
    for day, seen in panes.items():
        days[day]["panes"] = len(seen)
    return days


def selftest():
    with tempfile.TemporaryDirectory() as d:
        proj = os.path.join(d, ".claude", "projects", "-Users-y-work-munice")
        os.makedirs(proj)

        def claude(name, rows, entrypoint="cli"):
            with open(os.path.join(proj, name + ".jsonl"), "w") as f:
                for i, (ts, text) in enumerate(rows):
                    f.write(json.dumps({"type": "user", "uuid": f"{name}{i}", "timestamp": ts, "entrypoint": entrypoint,
                                        "message": {"content": text}}, ensure_ascii=False) + "\n")

        relay = "[pane-tell: '자동 작업' 세션이 보낸 글입니다. 요유가 이 pane에 직접 친 글이 아니므로, 되돌리기 어려운 일은 요유에게 확인하세요] "
        claude("main", [("2026-11-05T01:00:00Z", "이거 해줘"),  # 10:00 KST
                        ("2026-11-05T01:05:00Z", relay + "[Slack #0-yoyooyo 요유] 어디까지 됐어? (ts 1.2)"),
                        ("2026-11-05T01:06:00Z", relay + "[Slack #0-yoyooyo 요유 반응] '[결정 api#1]'에 :white_check_mark: (ts 1)"),
                        ("2026-11-05T01:07:00Z", relay + "밑 칸 w1:p3이 보고 없이 15분째 멈춰 있습니다"),
                        ("2026-11-05T01:08:00Z", "<task-notification>x</task-notification>"),
                        ("2026-11-05T16:00:00Z", "꺼도 돼?")])  # 11/06 01:00 KST
        claude("other", [("2026-11-05T02:00:00Z", "내가 할 거 있어?"), ("2026-11-05T02:01:00Z", "x" * 400 + " 어디까지"),
                         ("2026-11-05T02:02:00Z", "[ambient-round] work.md 를 읽고 어디까지 했는지 이어서")])
        claude("slackonly", [("2026-11-05T05:00:00Z", relay + "[Slack #0-yoyooyo 요유] 좋아 (ts 3.4)"),
                             # pane-tell's batch for the main pane (api#1051): 요유's words after another message
                             ("2026-11-05T05:01:00Z", relay + "밑 칸 w1:p3이 보고 없이 15분째 멈춰 있습니다\n\n" + relay +
                              "[Slack #0-yoyooyo 요유] 남은 거 있어? (ts 3.5)")])
        with open(os.path.join(proj, "main.jsonl"), "a") as f:  # typed while the agent worked: Claude keeps it as an attachment
            for i, (ts, prompt, mode, origin) in enumerate((
                    ("2026-11-05T01:10:00Z", "하고나서 QA까지하자", "prompt", "human"),
                    ("2026-11-05T01:11:00Z", [{"type": "text", "text": "[Image #1] 이거 남았어?"}, {"type": "image", "source": {}}], "prompt", "human"),
                    ("2026-11-05T01:12:00Z", relay + "[Slack #0-yoyooyo 요유 반응] '[결정 api#1]'에 :x: (ts 1)", "prompt", "human"),
                    ("2026-11-05T01:13:00Z", "Here is a note offered by a side agent:  > Heads up · 어디까지", "prompt", "human"),
                    ("2026-11-05T01:14:00Z", "<task-notification> x </task-notification>", "task-notification", "task-notification"),
                    ("2026-11-05T01:15:00Z", "<cross-session-message from=\"x\">어디까지</cross-session-message>", "prompt", "peer"),
                    ("2026-11-05T01:16:00Z", "Goal set: [auto-dispatch] 보드 일을 끝내세요", "prompt", "auto-continuation"))):
                f.write(json.dumps({"type": "attachment", "uuid": f"q{i}", "timestamp": ts, "attachment": {
                    "type": "queued_command", "prompt": prompt, "commandMode": mode, "origin": {"kind": origin}}}, ensure_ascii=False) + "\n")
            f.write(json.dumps({"type": "assistant", "uuid": "a0", "timestamp": "2026-11-05T01:17:00Z", "message": {"content": [  # its search
                {"type": "text", "text": "어디까지 셌는지 봅니다"}, {"type": "tool_use", "input": {"pattern": "queued_command"}}]}},
                ensure_ascii=False) + "\n")
        claude("sub", [("2026-11-05T03:00:00Z", "일입니다. 이 칸은 리드 밑 칸입니다. 어디까지"), ("2026-11-05T03:01:00Z", "계속")])
        claude("headless", [("2026-11-05T03:00:00Z", "어디까지 했어?")], entrypoint="sdk-cli")
        claude("old", [("2026-11-03T03:00:00Z", "다 됐어?")])
        cx = os.path.join(d, ".codex", "sessions", "2026", "11", "05")
        os.makedirs(cx)
        with open(os.path.join(cx, "rollout-2026-11-05T12-00-00-0001.jsonl"), "w") as f:
            for row in ({"type": "session_meta", "payload": {"originator": "codex-tui", "source": "cli"}},
                        {"type": "event_msg", "timestamp": "2026-11-05T04:00:00Z", "payload": {"type": "item_completed",
                         "item": {"type": "UserMessage", "content": [{"type": "text", "text": "얼마나 남았어"}]}}}):
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        got = count(d, "2026-11-05", "2026-11-06")
        day5, day6 = got["2026-11-05"], got["2026-11-06"]
        # main 1 + 2 typed while it worked, other 2 (the long one too), codex 1; not the sub-pane, headless, relays, notices,
        # a side agent's note, another session's message or /goal's
        assert day5["typed"] == 6, day5
        assert day5["slack"] == 3 and day5["reactions"] == 2, day5
        assert day5["panes"] == 3, "main, other and the Codex session: not a pane that only got Slack words"
        assert day5["questions"] == 5, "slack 어디까지, 남은 거(batched), 내가 할 거, 얼마나 남았어, 이거 남았어; not the 400-character one"
        assert day6 == {"typed": 1, "slack": 0, "reactions": 0, "panes": 1, "questions": 1}, day6
        assert "2026-11-03" not in got, "outside the window"
    assert options(["--list", "--since", "2026-11-04"]) == {"--list": True, "--since": "2026-11-04"}, "a flag before a pair"
    assert options(["--since", "2026-11-04", "--list", "--until", "2026-11-05"])["--until"] == "2026-11-05"
    print("selftest ok")


COLUMNS = (("typed", "직접 친 글"), ("slack", "Slack 글"), ("reactions", "Slack 반응"), ("panes", "답한 칸"), ("questions", "진행 질문 후보"))


def table(home: str, since: str, until: str, base: str, listing: list | None) -> str:
    lines = ["| 날짜 | " + " | ".join(n for _, n in COLUMNS) + " |", "|---|" + "---|" * len(COLUMNS)]

    def average(window: dict, a: str, b: str) -> str:
        n = (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days + 1
        return " | ".join(f"{sum(r[k] for r in window.values()) / n:.1f}" for k, _ in COLUMNS)

    trial = count(home, since, until, listing)
    for day in sorted(trial):
        lines.append(f"| {day} | " + " | ".join(str(trial[day][k]) for k, _ in COLUMNS) + " |")
    lines.append(f"| 시험 하루 평균 ({since}~{until}) | {average(trial, since, until)} |")
    a, b = base.split(":")
    lines.append(f"| 기준 하루 평균 ({a}~{b}, 같은 방법) | {average(count(home, a, b), a, b)} |")
    return "\n".join(lines)


def options(argv: list) -> dict:
    """`--name value` and bare `--name`, in any order: pairing argv two by two dropped --since after --list."""
    out, i = {}, 0
    while i < len(argv):
        takes = i + 1 < len(argv) and not argv[i + 1].startswith("--")
        out[argv[i]] = argv[i + 1] if takes else True
        i += 2 if takes else 1
    return out


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
        sys.exit()
    args = options(sys.argv[1:])
    hits = [] if "--list" in args else None
    print(table(os.path.expanduser("~"), args.get("--since", "2026-11-05"), args.get("--until", dt.datetime.now(KST).strftime("%Y-%m-%d")),
                args.get("--base", "2026-10-21:2026-11-04"), hits))
    if hits is not None:
        print("\n진행 질문 후보(손으로 가릴 것: 진행·차례 질문 / '왜 내가' 지적 / 아님):\n" + "\n".join(hits))
