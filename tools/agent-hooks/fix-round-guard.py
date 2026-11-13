#!/usr/bin/env python3
"""PreToolUse check (Claude Code and Codex): bound repeated fixes of one job.

On the night of 2026-11-05 the main pane ran check, fix, check, fix, check, fix from 01:09 to 04:15 while 요유 slept: four check
panes and three fix panes, and the third fix undid what the first two fixed. Its rules said "the fix's report gets a new check
pane" with no limit (daily wow 2026-11-05, card 01). After two fixes, the third proceeds only when the caller declares a known
local PR-only edit with FIX_ROUND_SCOPE=local-pr and FIX_ROUND_TARGETS=<absolute existing files, comma separated>. This is
the caller's scope assertion, not a detector of deployment intent. Do not declare it for operational services/data/settings,
an unknown fix, or an open-ended investigation. The fourth still requires an answer (daily wow 2026-11-13, item 3).
Otherwise the pane asks 요유 (the main pane through `decide`; a pane under a lead through its `남은 것:` report).
Repeating the call without the third-fix contract does not pass. 요유's own words after the refusal do: typed in the pane (also while the
agent works), or relayed from #0-yoyooyo by main-channel, where of reactions only ✅ answers (❌ is "no", 💤 "ask tomorrow").
An answer opens one fix of one job. Plain words open the job whose refusal came last before them. A ✅, and words that name
a board item, open a job only through its question: the first `decide` called after its refusal, unless another job's
refusal came first; they come after it and name its item (a ✅ on `[결정 api#1002]`). A ✅ on any other post opens nothing.
Once that fix's pane opened, the next fix of the job is refused again. The first version let any words of 요유's open every
job until the 05:00 /clear, and ❌ opened it too (check pane, 2026-11-05 13:18); the second let a ✅ on any decision asked
between the refusal and the next one open it (lead, 2026-11-05).

One job is one lineage, not one name: every round that night got a new name. The links are the report entries a spawn cites
("the 02:37:40 entry", "(02:37:40 항목)", as the short report hands them over). An entry's header names its pane's address,
and the spawn whose output printed that address (else whose work name matches the label) opened it. A fix is a spawn that
cites a check pane's report (`pane-spawn --check` names its pane `확인 …`); walking back from it, check to the work it checked
to that work's own spawn, counts the fixes before it.

Fixes delivered by pane-tell count too, including queued deliveries. A /clear still starts a new session record, so the count
starts over; preserving it across clears would need a record outside the session.
Unrelated words with no item in them still answer the latest refusal: the hook cannot read what they mean. A decide for
another item called first after the refusal takes the job's question, and a ✅ on its `[보류 중 …]` re-ask ("keep paused")
still matches; only the job's own item in its spawn would tell them apart. Items match by number alone (api#7 and cf#7 are
one), and only a `decide` whose command or output shows the item's URL ties it to a refusal. A decide before the job's
latest refusal (the spawn tried again before the answer) is no longer its question: that errs locked.
gcp-account-guard.py calls check(), for the reason pane-fit-guard.py gives. Fails open.

    fix-round-guard.py --selftest
"""
from __future__ import annotations  # a hook whose python3 is macOS's 3.9 would crash and let everything through

import json
import glob
import os
import re
import shlex
import subprocess
import sys
import time

sys.dont_write_bytecode = True  # no __pycache__ in the dotfiles at every tool call
MARK = "fix-round:"
LIMIT = 2  # an explicit concrete local-PR contract permits exactly one third fix
REASON = (MARK + " 같은 일의 고치기와 확인이 두 번 이상 돌았습니다({chain}). 추가 고치기를 멈춥니다. 세 번째만 고칠 내용이 정해진 로컬 PR 수정이면 "
          "FIX_ROUND_SCOPE=local-pr 및 FIX_ROUND_TARGETS=<수정할 기존 파일의 절대경로,쉼표로 구분>를 명령 앞에 붙여 진행합니다. "
          "운영 서비스·데이터·외부 설정을 건드리거나 고칠 내용이 미정이면 이 선언을 쓰지 않습니다. 네 번째는 선언으로 통과하지 않습니다. 지금까지 고친 것과 남은 결함을 "
          "`고를 것`·`추천`·`바뀌는 것` 세 줄로 묶어 요유에게 물으세요. 메인 칸은 다른 결정보다 먼저 그 일의 보드 항목으로 "
          "`python3 /Users/yoyoo/work/munice/munice-claude/.agents/skills/agent-board/main/main-channel.py decide --board <url>`을 부릅니다. "
          "이 거절 뒤 처음 부른 decide가 이 일의 질문입니다. 보드 항목이 없는 일이면 먼저 `echo \"<본문>\" | bash /Users/yoyoo/work/munice/.agents/skills/agent-board/board.sh "
          "create nightly_api \"<제목>\" yoyoo`(인프라 일은 terraform)로 항목을 만들고, 맨 처음 출력되는 URL을 `decide --board`에 넘깁니다. 제목과 본문에는 "
          "로컬 경로(보고 파일 경로 포함)를 쓰지 않습니다. 리드 밑 칸은 `남은 것:` 보고로 리드에게 올립니다. 이 거절 뒤에 요유가 칸에 직접 치거나 "
          "#0-yoyooyo에서 답하면(반응은 이 일의 질문 글에 단 ✅만 답으로 칩니다. ❌와 💤는 답이 아닙니다), 또는 결정 리스트의 이 일 행에서 상태를 "
          "추천대로로 바꾸거나 메모를 쓰면(안 함과 미룸은 답이 아닙니다) 이 일의 고치기 칸이 하나만 열립니다. 그 칸이 열린 뒤의 고치기는 다시 "
          "물어야 합니다. 그냥 다시 실행해서는 통과하지 않습니다.")
# The options that take a value may have it quoted (`--why "Slack 답글"`, api#1036); any other option's value is one bare word.
VALUED = r"--(?:why|cwd|kind)\s+(?:\"(?:\\.|[^\"\\])*\"|'[^']*'|\S+)"
ASSIGN = r"[A-Za-z_]\w*=(?:\"(?:\\.|[^\"\\])*\"|'[^']*'|[^\s;&|()]*)[ \t]+"
SPAWN = re.compile(r"(?:^|[;&|(\n])\s*(?:" + ASSIGN + r")*(?:\S*/)?pane-spawn("
                   r"(?:\s+" + VALUED + r"|\s+--[\w-]+(?:\s+[^\s\"'-]\S*)?)*)\s+"
                   r"(\"(?:\\.|[^\"\\])*\"|'[^']*')")
TELL = re.compile(r"(?:^|[;&|(\n])\s*(?:" + ASSIGN + r")*(?:\S*/)?pane-tell\s+"
                  r"[\"']?((?:\w+/)?w\w+:p\w+)[\"']?\s+")
CITE = re.compile(r"(\d{2}:\d{2}:\d{2})\s*(?:entry|항목)")
CITED_DAY = re.compile(r"pane-reports/(\d{4}-\d{2}-\d{2})\.md")
ENTRY = re.compile(r"^## (\d{2}:\d{2}:\d{2}) (\S+) ?(.*)$", re.M)  # auto-tab-close.py writes `## HH:MM:SS <address> <label>`
ADDRESS = re.compile(r"(?:^|\n)((?:\w+/)?w\w+:p\w+)[ \t]*(?=\n|$)")  # pane-spawn prints the pane alone on a line once it opened
CODEX_CMD = re.compile(r"\bcmd[\"']?\s*:\s*(\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')")  # as in yoyoo-share-guard.py
RELAYED_YOYOO = re.compile(r"^\[pane-tell: '자동 작업' [^\]]*\] \[Slack #\S+ 요유( 반응)?\]")  # main-channel's relay of 요유's words
RELAYED_LIST = re.compile(r"^\[pane-tell: '자동 작업' [^\]]*\] \[Slack 리스트 요유\] ")  # main-channel's relay of a row 요유 changed
BATCH = re.compile(r"\n\n(?=\[pane-tell: ')")  # between the messages pane-tell held for the main pane and sends as one prompt
REACTION = re.compile(r".*에 :([\w+-]+): \(", re.S)  # greedy: the relay's own `에 :emoji: (ts …)` comes after the quoted post
NOT_TYPED = ("[pane-tell:", "[auto-dispatch]", "<", "[main-refresh]", "[handoff:", "Stop hook feedback", "Caveat:", "[Request interrupted",
             "This session is being continued", "# AGENTS.md", "Here is a note offered by a side agent")
REF = re.compile(r"\b[a-z]+#(\d+)\b")  # a board item as 요유 sees it in Slack: `[결정 api#1002]`; the channel's `#0-…` has no letters before it
DECIDE = re.compile(r"/issues/(\d+)")  # in a `main-channel.py decide --board <url>` call or its output (a `$url` prints at create)


def unquote(literal: str) -> str:
    if literal.startswith("'"):
        return literal[1:-1]
    try:
        return json.loads(literal)
    except ValueError:
        return literal[1:-1]


def simple_commands(command: str) -> list[str]:
    """Split shell command boundaries outside quotes, escapes and comments.

    Keep original quoting for the pane argument parser. This is lexical only;
    it does not evaluate shell expansions or execute any command.
    """
    commands, part = [], []
    quote, escaped, comment = "", False, False
    i = 0
    while i < len(command):
        char = command[i]
        if comment:
            if char != "\n":
                i += 1
                continue
            comment = False
        if not escaped and quote != "'" and (command.startswith("$(", i) or char == "`"):
            # Substitutions have their own quote context. Keep their source as
            # one outer word; pane_commands separately checks their execution.
            opening = 2 if char == "$" else 1
            _, end = substitution_regions(command, i + opening, ")" if char == "$" else "`")
            part.append(command[i:end])
            i = end
            continue
        if escaped:
            part.append(char)
            escaped = False
        elif char == "\\" and quote != "'":
            part.append(char)
            escaped = True
        elif quote:
            part.append(char)
            if char == quote:
                quote = ""
        elif char in "\"'`":
            part.append(char)
            quote = char
        elif char == "#" and (not part or part[-1].isspace()):
            comment = True
        elif char in ";&|()\n":
            if "".join(part).strip():
                commands.append("".join(part).strip())
            part = []
        else:
            part.append(char)
        i += 1
    if "".join(part).strip():
        commands.append("".join(part).strip())
    return commands


def substitution_regions(command: str, start=0, closing="") -> tuple[list[str], int]:
    """Find executable substitutions without evaluating shell expansions.

    A substitution starts a fresh quote context even inside double quotes.
    Single quotes, escaped dollars/backticks and comments remain display text.
    """
    bodies, quote, i = [], "", start
    while i < len(command):
        char = command[i]
        if char == "\\" and quote != "'":
            i += 2
            continue
        if quote == "'":
            if char == "'":
                quote = ""
            i += 1
            continue
        if not quote and char == "#" and (i == start or command[i - 1].isspace() or command[i - 1] in ";&|()"):
            newline = command.find("\n", i)
            i = len(command) if newline < 0 else newline + 1
            continue
        if command.startswith("$(", i):
            _, end = substitution_regions(command, i + 2, ")")
            bodies.append(command[i + 2:end - 1])
            i = end
            continue
        if char == "`":
            if closing == "`":
                return bodies, i + 1
            _, end = substitution_regions(command, i + 1, "`")
            bodies.append(command[i + 1:end - 1])
            i = end
            continue
        if quote:
            if char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char == closing:
            return bodies, i + 1
        elif char == "(" and closing == ")":
            # Ordinary grouping parentheses also balance a substitution body.
            _, i = substitution_regions(command, i + 1, ")")
            continue
        i += 1
    return bodies, i


def pane_commands(command: str) -> list[str]:
    parts = [part for part in simple_commands(command) if SPAWN.match(part) or TELL.match(part)]
    for body in substitution_regions(command)[0]:
        parts.extend(pane_commands(body))
    return list(dict.fromkeys(parts))


def concrete_local_fix(command: str) -> bool:
    """The caller declares a known PR-only edit, not a production-operation authorization.

    Only assignments attached to the actual pane command and existing files in a
    Git worktree qualify. The declaration carries the agent's scope judgment;
    separate operational permission guards still apply.
    """
    parts = pane_commands(command)
    if not parts:
        return False
    return all(local_contract(part) for part in parts)


def local_contract(command: str) -> bool:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    values = {}
    for token in tokens:
        if not re.match(r"^[A-Za-z_]\w*=", token):
            break
        key, value = token.split("=", 1)
        values[key] = value
    targets = values.get("FIX_ROUND_TARGETS", "").split(",")
    return (values.get("FIX_ROUND_SCOPE") == "local-pr" and bool(targets)
            and all(worktree_file(p) for p in targets))


def worktree_file(path: str) -> bool:
    """Resolve symlinks before checking repository membership, including linked worktrees."""
    if not os.path.isabs(path) or not os.path.isfile(path):
        return False
    resolved = os.path.realpath(path)
    try:
        result = subprocess.run(["git", "-C", os.path.dirname(path), "rev-parse", "--show-toplevel", "--is-inside-work-tree"],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=2)
        lines = result.stdout.splitlines()
        return (result.returncode == 0 and len(lines) == 2 and lines[1] == "true"
                and os.path.commonpath([resolved, os.path.realpath(lines[0])]) == os.path.realpath(lines[0]))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


def flat(content) -> str:
    if isinstance(content, str):
        try:
            obj = json.loads(content)
            if isinstance(obj, dict) and isinstance(obj.get("output"), str):
                return obj["output"]
        except ValueError:
            pass
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
                a = d["attachment"]  # typed while the agent worked; the rest is a background task, another session or /goal
                if a.get("commandMode", "prompt") == "prompt" and (a.get("origin") or {}).get("kind", "human") == "human":
                    out.append(("in", flat(a.get("prompt"))))
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
    m = SPAWN.match(command)
    if not m:
        return None
    text = command[m.start():]
    flags = re.sub(VALUED, "", m.group(1))  # a reason that says --check is no flag (Codex review, api#1036)
    return {"check": bool(re.search(r"--check\b", flags)), "name": unquote(m.group(2)), "cites": CITE.findall(text),
            "days": CITED_DAY.findall(text), "addr": None, "refused": None}


def action(command: str) -> dict | None:
    parts = pane_commands(command)
    return simple_action(parts[0]) if parts else None


def simple_action(command: str) -> dict | None:
    opened = spawn(command)
    if opened:
        return opened
    m = TELL.match(command)
    if not m:
        return None
    text = command[m.start():]
    return dict(check=False, name=m[1], cites=CITE.findall(text), days=CITED_DAY.findall(text),
                target=m[1].split("/")[-1], addr=None, refused=None)


def bare(label: str) -> str:
    return label.lstrip("🔴🟢👑🫡 ").strip()


def is_check(entry: tuple) -> bool:
    return bare(entry[3]).startswith("확인 ")


def answers(text: str) -> str:
    """"react" for 요유's ✅ relayed from Slack, "word" for their own words, typed or relayed, else "". ❌ is "no" and 💤 "ask me
    tomorrow": no answer."""
    s = text.lstrip()
    if RELAYED_LIST.match(s):  # in the decision list 추천대로 is the ✅ and a memo is words; 안 함 and 미룸 alone answer nothing
        return "react" if " · 상태: 추천대로" in s else "word" if " · 메모: " in s else ""
    m = RELAYED_YOYOO.match(s)
    if m and m.group(1):
        emoji = REACTION.match(s)
        return "react" if emoji and emoji.group(1) == "white_check_mark" else ""
    return "word" if m or (s and not s.startswith(NOT_TYPED)) else ""


def check(payload: dict, env) -> str | None:
    command = str((payload.get("tool_input") or {}).get("command") or "")
    if payload.get("tool_name") != "Bash" or not CITE.search(command):
        return None
    # Each executable pane action must qualify on its own, including actions
    # nested in substitutions inside another pane action's arguments.
    for part in pane_commands(command):
        single = dict(payload, tool_input=dict(payload.get("tool_input") or {}, command=part))
        reason = check_action(single, env)
        if reason:
            return reason
    return None


def check_action(payload: dict, env) -> str | None:
    command = str((payload.get("tool_input") or {}).get("command") or "")
    new = simple_action(command)
    if not new or new["check"]:
        return None
    try:
        record = events(payload.get("transcript_path") or "")
    except OSError:
        return None
    spawns, by_id, asks, words = [], {}, {}, []
    for i, e in enumerate(record):
        if e[0] == "call" and ("pane-spawn" in e[1] or "pane-tell" in e[1]):
            s = action(e[1])
            if s:
                spawns.append(s)
                by_id[e[2]] = s
        elif e[0] == "call" and "main-channel" in e[1] and " decide" in e[1]:
            asks[e[2]] = (i, set(DECIDE.findall(e[1])))
        elif e[0] == "out":
            s, opened = by_id.get(e[2]), ADDRESS.search(e[1])
            if s is not None and MARK in e[1] and e[3]:  # this hook refused that spawn: a read of the text is no refusal
                s["refused"] = i
            elif s is not None and opened:
                s["addr"] = opened.group(1).split("/")[-1]
            elif s is not None and s.get("target") and not e[3] and re.search(r"(?:^|\n)(?:told |queued for )", e[1]):
                s["addr"] = s["target"]
            if e[2] in asks:
                asks[e[2]][1].update(DECIDE.findall(e[1]))
        elif e[0] == "in":
            for part in BATCH.split(e[1]):  # pane-tell's batch for the main pane: each message keeps its own mark
                if said := answers(part):
                    words.append((i, set(REF.findall(part)), said == "react"))
    folder = env.get("PANE_REPORT_DIR") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".local", "state", "pane-reports")
    files = {}
    state = env.get("XDG_STATE_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".local/state")
    automatic_folder = os.path.join(state, "orchestrator/automatic-checks")
    terminals = {}
    for path in glob.glob(os.path.join(automatic_folder, "*.json")):
        try:
            with open(path) as f:
                context = json.load(f)
            address, terminal = context.get("worker_address"), context.get("worker_terminal")
            if address and terminal:
                terminals.setdefault(address.split("/")[-1], set()).add(terminal)
        except (OSError, ValueError, TypeError, AttributeError):
            continue

    def same_worker(a, b):
        # Pane moves change addresses; only an unambiguous stable terminal can bridge them.
        return a == b or bool(a and b and len(terminals.get(a, ())) == 1 and terminals.get(a) == terminals.get(b))

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

    def walk(start: dict, known: list) -> tuple:
        """start's fixes, newest first, and the spawn its job began with: the job's key."""
        def before(s):
            return next((i for i, item in enumerate(known) if item is s), len(known))

        def opener(e: tuple, end: int) -> dict | None:
            # A reused worker has the same address every round: only actions before its checker can own that report.
            candidates = [s for s in known[:end] if not s.get("target") or
                          any(is_check(r) for r in (entry(s, at) for at in s["cites"]) if r)]
            by_addr = [s for s in candidates if same_worker(s["addr"], e[2])]
            by_name = [s for s in candidates if s["addr"] is None and (f"확인 {s['name']}" if s["check"] else s["name"]) == bare(e[3])]
            return (by_addr or by_name or [None])[-1]

        chain, cur, seen = [], start, set()
        while cur is not None and id(cur) not in seen:
            seen.add(id(cur))
            checks = [e for e in (entry(cur, at) for at in cur["cites"]) if e and is_check(e)]
            if cur["check"] or not checks:
                break
            chain.append(cur)
            checked = max(checks)
            checker = opener(checked, before(cur))
            works = [e for e in (entry(checker, at) for at in checker["cites"]) if e and not is_check(e)] if checker else []
            if not checker and (token := re.search(r"\[ac-([0-9a-f]{16})\]", checked[3])):
                # Stop hooks open automatic checks outside the main transcript. Their persisted handoff is the link.
                try:
                    with open(os.path.join(automatic_folder, token[1] + ".json")) as f:
                        context = json.load(f)
                    source = dict(days=CITED_DAY.findall(context["report"]))
                    works = [e for e in (entry(source, at) for at in CITE.findall(context["entry"])) if e and not is_check(e)]
                except (OSError, ValueError, KeyError, TypeError):
                    pass
            cur = opener(max(works), before(checker) if checker else before(cur)) if works else None
        return chain, cur or chain[-1]

    chain, job = walk(new, [s for s in spawns if s["refused"] is None])
    if len(chain) <= LIMIT:
        return None
    if len(chain) == LIMIT + 1 and local_contract(command):
        return None
    # Each fix past the limit, as the hook saw it then: refused, or let through and its pane opened (that used the answer up).
    rounds = []
    for k, s in enumerate(spawns):
        if not s["check"] and (s["refused"] is not None or s["addr"]):
            past, key = walk(s, [x for x in spawns[:k] if x["refused"] is None])
            if len(past) > LIMIT:
                rounds.append((s, key))
    mine = [s for s, key in rounds if key is job]
    refusals = [(s["refused"], key) for s, key in rounds if s["refused"] is not None]

    def asked(i: int):  # an answer goes to the refusal last before it, so 요유's words open one job
        before = [key for at, key in refusals if at < i]
        return before[-1] if before else None

    if mine and mine[-1]["refused"] is not None:
        at = mine[-1]["refused"]
        # This refusal's question: the first decide after it, unless another job's refusal came first. Wrong, it stays locked.
        first, item = min((a for a in asks.values() if a[0] > at), key=lambda a: a[0], default=(at, set()))
        if asked(first) is not job:
            item = set()
        for j, named, react in words:
            if j < at:
                continue
            if named or react:  # a ✅, or words naming an item, answer that question alone
                if named & item and j > first:
                    return None
            elif asked(j) is job:
                return None
    return REASON.format(chain=" → ".join(s["name"] for s in reversed(chain)))


def selftest():
    import tempfile

    # The night's reports are in 2026-11-05.md, and a citation with no day is looked up today and yesterday: the clock stays
    # on that day, or the selftest fails from 11/07 on (it did, and refused every dotfiles push).
    time.time = lambda: 1793851200.0  # 2026-11-05 13:00 KST
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["git", "init", "-q", d], check=True)
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

        def ran(command, i, output, error=False):
            return [{"type": "assistant", "message": {"content": [{"type": "tool_use", "id": i, "name": "Bash", "input": {"command": command}}]}},
                    {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": i, "content": output, "is_error": error}]}}]

        def claude_lines(rounds, extra=(), tag="t"):
            lines = []
            for i, (at, addr, flags, name, cited) in enumerate(rounds):
                lines += ran(spawn_cmd(flags, name, cited), f"{tag}{i}", f"workers/{addr}\n")
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
        target = os.path.join(d, "specific-fix.py")
        with open(target, "w") as f:
            f.write("# local test target\n")
        local = f"FIX_ROUND_SCOPE=local-pr FIX_ROUND_TARGETS={target} "
        for format_name, lines in (("claude", claude_lines(night)), ("codex", codex_lines(night))):
            scenario = transcript(lines, f"local-{format_name}.jsonl")
            assert check(p(local + new, scenario), env) is None, f"{format_name}: a concrete local-only third fix proceeds"
            assert check(p(f"FIX_ROUND_SCOPE=production FIX_ROUND_TARGETS={target} " + new, scenario), env), "operational fixes still require an answer"
            assert check(p("FIX_ROUND_SCOPE=local-pr " + new, scenario), env), "an unknown target still requires an answer"
            assert check(p("FIX_ROUND_SCOPE=local-pr FIX_ROUND_TARGETS= " + new, scenario), env), "an empty target declaration still requires an answer"
            assert check(p(f"FIX_ROUND_SCOPE=local-pr FIX_ROUND_TARGETS={d} " + new, scenario), env), "a whole directory is not a concrete target"
        assert check(p('# ; ' + local + new + '\n' + new, path), env), "a comment's contract cannot authorize a third fix"
        assert check(p(local + 'pane-spawn "unrelated" "no citation"; ' + new, path), env), "a different pane command's contract cannot authorize a third fix"
        assert check(p('echo "display; pane-spawn \'fake\' \'no citation\'"; ' + local + new, path), env) is None, "quoted pane text cannot hide a real local contract"
        for probe in ('echo "$(' + new + ')"', 'out=$(' + new + ')', 'echo `' + new + '`', 'echo "$(echo $(' + new + '))"'):
            assert check(p(probe, path), env), "executable command substitutions cannot bypass the third-fix check"
        assert check(p('echo "$(' + local + new + ')"', path), env) is None, "a substitution's own local contract qualifies"
        assert check(p(local + 'echo "$(' + new + ')"', path), env), "an outer command's contract cannot authorize its substitution"
        assert check(p(local + 'pane-spawn "unrelated" "$(' + new + ')"', path), env), "a nested pane action needs its own contract"
        assert check(p("echo '$(" + new.replace("'", "") + ")'", path), env) is None, "single-quoted substitution is display text"
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

        # Reusing one worker must preserve the same two-fix limit on both transcript formats.
        reused = []
        for i, (at, addr, flags, name, cited) in enumerate(night):
            addr = addr if flags else night[0][1]
            report(at, f"workers/{addr}", f"🟢 🫡 {'확인 ' if flags else ''}{name}")
            command = spawn_cmd(flags, name, cited) if flags or i == 0 else f"pane-tell workers/{addr} '{cite(cited)} Fix it.'"
            reused += ran(command, f"r{i}", f"workers/{addr}\n" if flags or i == 0 else f"queued for workers/{addr} (Job)\n")
        tell_third = f"pane-tell workers/{night[0][1]} '{cite(third[4])} Fix it.'"
        assert check(p(tell_third, transcript(reused, "reuse.jsonl")), env), "Claude: third fix on the same worker"
        assert check(p(local + tell_third, transcript(reused, "reuse-local.jsonl")), env) is None, "Claude: a concrete local third fix reuses its worker"
        codex_reused = []
        for i in range(0, len(reused), 2):
            command = reused[i]["message"]["content"][0]["input"]["command"]
            output = reused[i + 1]["message"]["content"][0]["content"]
            codex_reused += [{"type": "response_item", "payload": {"type": "custom_tool_call", "name": "exec", "call_id": f"r{i}",
                              "input": f"text(await tools.exec_command({{cmd:{json.dumps(command)}}}))"}},
                            {"type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": f"r{i}",
                              "output": json.dumps(dict(exit_code=0, output=output))}}]
        assert check(p(tell_third, transcript(codex_reused, "reuse-codex.jsonl")), env), "Codex: third fix on the same worker"
        assert check(p(local + tell_third, transcript(codex_reused, "reuse-local-codex.jsonl")), env) is None, "Codex: a concrete local third fix reuses its worker"
        lookup = ran(f"pane-tell workers/{night[0][1]} '{cite(night[4][0])} Additional lookup.'", "lookup",
                     f"told workers/{night[0][1]} (Job)\n")
        assert check(p(tell_third, transcript(reused + lookup, "reuse-lookup.jsonl")), env), "a lookup cannot reset the fix lineage"
        automatic = os.path.join(d, ".local/state/orchestrator/automatic-checks")
        os.makedirs(automatic)
        for i in (1, 3, 5):
            at, addr, flags, name, cited = night[i]
            token = f"{i:016x}"
            report(at, f"workers/{addr}", f"🟢 🫡 확인 {name} [ac-{token}]")
            with open(os.path.join(automatic, token + ".json"), "w") as f:
                json.dump(dict(report=os.path.join(reports, "2026-11-05.md"), entry=f"{cited} 항목"), f)
        no_check_calls = [event for i in (0, 2, 4) for event in reused[2*i:2*i+2]]
        assert check(p(tell_third, transcript(no_check_calls, "reuse-automatic.jsonl")), env), "automatic checks keep the lineage without transcript calls"
        # A move between a successful fix delivery and its report must not reset the limit.
        for i in (1, 3, 5):
            context_path = os.path.join(automatic, f"{i:016x}.json")
            with open(context_path) as f:
                context = json.load(f)
            context.update(worker_terminal="stable-worker", worker_address="workers/" + (night[0][1] if i == 1 else "w2:p7"))
            with open(context_path, "w") as f:
                json.dump(context, f)
        for i in (2, 4):
            at, addr, flags, name, cited = night[i]
            report(at, "workers/w2:p7", f"🟢 🫡 {name}")
        assert check(p(tell_third, transcript(no_check_calls, "reuse-moved.jsonl")), env), "moved worker keeps the fix lineage"
        assert check(p(f"pane-tell workers/{night[0][1]} '{cite(night[3][0])} Fix it.'",
                       transcript(reused[:8], "reuse-second.jsonl")), env) is None, "second fix on the same worker passes"
        for at, addr, flags, name, cited in night:
            report(at, f"workers/{addr}", f"🟢 🫡 {'확인 ' if flags else ''}{name}")

        # Only the third fix stops: the second fix, a check, a first job and other commands pass.
        assert check(p(spawn_cmd("", "메인 칸 위임 틈 수정", "02:37:40"), transcript(claude_lines(night[:4]), "second.jsonl")), env) is None, "second fix"
        assert check(p(spawn_cmd("--check ", "틈 수정 확인", "03:04:44"), path), env) is None, "a check is never stopped"
        assert check(p(spawn_cmd("", "새 일", None), path), env) is None, "a job citing no report"
        assert check(p(spawn_cmd("", "다른 일", "03:04:44"), path), env) is None, "citing a work pane's report: not a fix"
        assert check(p("pane-tell workers/w1:p15 '고쳐 주세요 03:34:57 항목'", path), env), "a third fix via pane-tell is also refused"
        assert check(p("pane-tell workers/w1:p15 '추가 조회 03:04:44 항목'", path), env) is None, "a work-report lookup is not a fix"
        assert check({"tool_name": "Edit", "tool_input": {"file_path": "/x"}, "transcript_path": path}, env) is None
        assert check(p(new, os.path.join(d, "missing.jsonl")), env) is None, "fails open"

        # A pane found by its name when the spawn printed no address (an older pane-spawn, or output cut short).
        no_addr = [x if x["type"] == "assistant" else {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": x["message"]["content"][0]["tool_use_id"], "content": "(no output)"}]}}
            for x in claude_lines(night)]
        assert check(p(new, transcript(no_addr, "n.jsonl")), env), "matched by name"

        # Repeating does not pass; 요유's own words after the refusal do, typed or relayed from #0-yoyooyo.
        assert "board.sh create" in reason and "decide --board" in reason, "a job with no board item gets one before decide"
        refused = ran(new, "tX", f"PreToolUse:Bash hook error: {reason}", error=True)
        read_back = ran("sed -n 1,9p notes.md", "tY", reason)
        read_failed = ran("sed -n 1,9p notes.md; ls nothing", "tZ", reason, error=True)  # a read that exited 1 on the refusal's text

        def said(text):
            return {"type": "user", "message": {"content": text}}

        def queued(text, mode="prompt", origin="human"):  # typed while the agent worked
            return {"type": "attachment", "attachment": {"type": "queued_command", "prompt": text, "commandMode": mode, "origin": {"kind": origin}}}

        def judged(command, *extra):
            return check(p(command, transcript(claude_lines(night, list(extra)))), env)

        assert judged(new, *read_back, said("계속해")), "the text read back from a file is no refusal"
        assert judged(new, *read_failed, said("계속해")), "nor is a failed read of it: only a pane-spawn call is refused"

        relay = "[pane-tell: '자동 작업' 세션이 보낸 글입니다. 요유가 이 pane에 직접 친 글이 아니므로, 되돌리기 어려운 일은 요유에게 확인하세요] "
        decide = ran("python3 /x/main-channel.py decide --board https://github.com/munice-dev/nightly_api/issues/1002 - <<'EOF'\n고를 것: …\nEOF",
                     "tD", "ts 1.5")
        other = ran("python3 /x/main-channel.py decide --board https://github.com/munice-dev/nightly_api/issues/1003 - <<'EOF'\n고를 것: …\nEOF",
                    "tD2", "ts 1.6")  # another decision the main pane asked in the same window
        yes = relay + "[Slack #0-yoyooyo 요유 반응] '[결정 api#1002] 고를 것: …'에 :white_check_mark: (ts 1)"
        assert judged(new, *refused), "the same call again"
        for words in ([said(relay + "[Slack #0-yoyooyo 요유] 응 계속 고쳐 (ts 1.2)")], [said("계속해")], [queued("계속해")],
                      # pane-tell's batch for the main pane: 요유's words after another message, each with its mark (api#1051)
                      [said(relay + "밑 칸 w1:p15이 보고 없이 15분째 멈춰 있습니다\n\n" + relay + "[Slack #0-yoyooyo 요유] 응 계속 고쳐 (ts 1.2)")],
                      [*decide, queued(relay + "밑 칸 w1:p15이 질문 창에서 멈춰 있습니다\n\n" + yes)],
                      [queued([{"type": "text", "text": "[Image #1] 계속해"}, {"type": "image"}])],
                      [*decide, said(yes)], [*decide, *other, said(yes)],
                      [*decide, said(relay + "[Slack #0-yoyooyo 요유] api#1002 추천대로 (스레드 1.1: '11/05 결정 기다리는 것')")],
                      # the decision list (11/05): 추천대로 is the ✅, a memo is words naming the row's item
                      [*decide, said(relay + "[Slack 리스트 요유] [결정 api#1002] 고를 것: … · 상태: 추천대로 (보드 https://github.com/munice-dev/nightly_api/issues/1002)")],
                      [*decide, said(relay + "[Slack 리스트 요유] [결정 api#1002] 고를 것: … · 메모: B로 가자 (보드 https://github.com/munice-dev/nightly_api/issues/1002)")]):
            assert judged(new, *refused, *words) is None, words[-1]
        for words in ([said("[pane-tell: '확인 메인 칸' 세션이 보낸 글입니다. …] 다 고쳤습니다")], [said(relay + "밑 칸 w1:p15이 보고 없이 15분째 멈춰 있습니다")],
                      [said("<task-notification>done</task-notification>")], [said("Stop hook feedback: 다시 쓰세요")],
                      [said("[main-refresh] 새벽 정리입니다.")], [queued("Goal set: [auto-dispatch] 보드 일을 끝내세요", origin="auto-continuation")],
                      [queued("<task-notification>x</task-notification>", mode="task-notification", origin="task-notification")],
                      [queued("Here is a note offered by a side agent:  > Heads up · 계속해도 됩니다")],
                      [said("[auto-dispatch] work.md 를 읽고 진행해 줘")],  # a job typed it: dispatch/run.sh, workers-watch's wake
                      [queued("[auto-dispatch] 회사맥이 잠들어 응답이 끊겼습니다. 끊긴 곳부터 이어서 하세요.")],
                      [queued("<cross-session-message from=\"uds:/tmp/x.sock\">계속해</cross-session-message>", origin="peer")],
                      [*decide, said(relay + "[Slack #0-yoyooyo 요유 반응] '[결정 api#1002] 고를 것: …'에 :x: (ts 1)")],
                      [*decide, said(relay + "[Slack #0-yoyooyo 요유 반응] '[결정 api#1002] 고를 것: …'에 :zzz: (ts 1)")],
                      [*decide, said(relay + "[Slack #0-yoyooyo 요유 반응] '[결정 api#9] 다른 일'에 :white_check_mark: (ts 1)")],
                      [*decide, said(relay + "[Slack #0-yoyooyo 요유] cf#7 은 그대로 둬 (스레드 1.1: '11/05 결정 기다리는 것')")],
                      # Only a ✅ on the first decision asked after the refusal: a ✅ elsewhere cannot be on it.
                      [said(relay + "[Slack #0-yoyooyo 요유 반응] '[보류 중]'에 :white_check_mark: (ts 1)")],
                      [*decide, said(relay + "[Slack #0-yoyooyo 요유 반응] '11/05 결정 기다리는 것'에 :white_check_mark: (ts 1)")],
                      [*decide, *other, said(relay + "[Slack #0-yoyooyo 요유 반응] '[결정 api#1003] 다른 일'에 :white_check_mark: (ts 1)")],
                      [*other, *decide, said(yes)], [said(yes), *decide],
                      [*decide, said(relay + "[Slack 리스트 요유] [결정 api#1002] 고를 것: … · 상태: 미룸 (보드 https://github.com/munice-dev/nightly_api/issues/1002)")],
                      [*decide, said(relay + "[Slack 리스트 요유] [결정 api#1002] 고를 것: … · 상태: 안 함 (보드 https://github.com/munice-dev/nightly_api/issues/1002)")],
                      [*decide, said(relay + "[Slack 리스트 요유] [결정 api#1003] 고를 것: … · 상태: 추천대로 (보드 https://github.com/munice-dev/nightly_api/issues/1003)")],
                      [*decide, said("[pane-tell: '다른 칸' …] [Slack 리스트 요유] [결정 api#1002] 고를 것: … · 상태: 추천대로 (보드 x)")]):
            assert judged(new, *refused, *words), f"not 요유's answer to this refusal: {words[-1]}"
        assert judged(new, said("계속해"), *refused), "words before the refusal answer nothing"

        # The answer is for the refused job, and for one fix: once that fix's pane opened, the next fix asks again.
        job_c = [("05:00:00", "w1:pC0", "", "C 일", None), ("05:01:00", "w1:pC1", "--check ", "C 일 확인", "05:00:00"),
                 ("05:02:00", "w1:pC2", "", "C 수정1", "05:01:00"), ("05:03:00", "w1:pC3", "--check ", "C 수정1 확인", "05:02:00"),
                 ("05:04:00", "w1:pC4", "", "C 수정2", "05:03:00"), ("05:05:00", "w1:pC5", "--check ", "C 수정2 확인", "05:04:00")]
        for at, addr, flags, name, cited in job_c:
            report(at, f"workers/{addr}", f"🟢 🫡 {'확인 ' if flags else ''}{name}")
        new_c = spawn_cmd("", "C 수정3", "05:05:00")
        both = claude_lines(job_c, tag="c")
        assert judged(new_c, *both), "job C's third fix"
        assert judged(new_c, *both, *refused, said("계속해")), "an answer to job A's refusal does not open job C"
        assert judged(new, *both, *refused, said("계속해")) is None, "while it opens job A"
        refused_c = ran(new_c, "tXC", f"PreToolUse:Bash hook error: {reason}", error=True)
        assert judged(new, *both, *refused, *refused_c, said("계속해")), "plain words answer the latest refusal, C's, not A's"
        assert judged(new_c, *both, *refused, *refused_c, said("계속해")) is None, "C's"
        assert judged(new, *both, *refused, *decide, *refused_c,
                      said(relay + "[Slack #0-yoyooyo 요유 반응] '[결정 api#1002] 고를 것: …'에 :white_check_mark: (ts 1)")) is None, \
            "a reaction answers the refusal its decision was asked for"
        assert judged(new, *both, *refused, *refused_c, *decide,
                      said(relay + "[Slack #0-yoyooyo 요유 반응] '[결정 api#1002] 고를 것: …'에 :white_check_mark: (ts 1)")), \
            "a decision asked after C's refusal is C's"

        report("04:00:20", "workers/w1:p15", "🟢 🫡 메인 칸 잡일 예외 되돌리기")
        report("04:30:00", "workers/w1:p16", "🟢 🫡 확인 잡일 예외 되돌리기 확인")
        third_ran = ran(new, "tP", "workers/w1:p15\n")
        third_check = ran(spawn_cmd("--check ", "잡일 예외 되돌리기 확인", "04:00:20"), "tQ", "workers/w1:p16\n")
        fourth = spawn_cmd("", "메인 칸 잡일 예외 다시 고치기", "04:30:00")
        local_third_ran = ran(local + new, "local-third", f"workers/{third[1]}\n")
        assert judged(local + fourth, *local_third_ran, *third_check), "a declared local third fix is counted before the fourth"
        assert judged(local + fourth, *third_ran, *third_check), "the local-only declaration never opens a fourth fix"
        spaced_target = os.path.join(d, "specific fix.py")
        with open(spaced_target, "w") as f:
            f.write("# another concrete target\n")
        quoted_local = f"FIX_ROUND_SCOPE=local-pr FIX_ROUND_TARGETS={shlex.quote(spaced_target)} "
        assert judged(quoted_local + fourth, *third_ran, *third_check), "quoted target paths cannot bypass the fourth-fix limit"
        assert judged(fourth, *refused, said("계속해"), *third_ran, *third_check), "the answer was used up by the third fix"
        refused_4 = ran(fourth, "tX4", f"PreToolUse:Bash hook error: {reason}", error=True)
        assert judged(fourth, *refused, said("계속해"), *third_ran, *third_check, *refused_4, said("응 한 번 더")) is None, "a new answer"
        third_failed = ran(new, "tF", "pane-spawn: agent_pane_busy\n", error=True)
        assert judged(new, *refused, said("계속해"), *third_failed) is None, "a spawn that opened no pane used nothing up"

        codex_refused = [{"type": "response_item", "payload": {"type": "function_call", "name": "exec_command", "call_id": "cX",
                                                                "arguments": json.dumps({"cmd": new}, ensure_ascii=False)}},
                         {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "cX",
                                                                "output": f"Command blocked by PreToolUse hook: {reason}"}}]
        codex_said = {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "그래 한 번 더"}]}}
        assert check(p(new, transcript(codex_lines(night, codex_refused), "c2.jsonl")), env), "Codex: the same call again"
        assert check(p(new, transcript(codex_lines(night, [*codex_refused, codex_said]), "c3.jsonl")), env) is None, "Codex: 요유 answered"

    # api#1036: --why takes free text, often quoted; the work name is the first quoted word after the options, not the reason.
    for cmd in ('pane-spawn --why "Slack 답글" --cwd /w "api#1 고치기" "x"', "pane-spawn --kind claude --why 'gcloud 배포' \"api#1 고치기\" 'x'",
                'pane-spawn --why slack "api#1 고치기" "x"', 'pane-spawn --check "api#1 고치기" "x"'):
        assert (spawn(cmd) or {}).get("name") == "api#1 고치기", f"the work name, not an option's value: {cmd}"
    assert (spawn('pane-spawn --check "api#1 고치기" "x"') or {}).get("check"), "--check is a flag"
    for cmd in ('pane-spawn --why "Codex --check cannot use network" "api#1 고치기" "x"', "pane-spawn --why '--check' \"a\" \"x\"",
                'pane-spawn --why --check "a" "x"'):  # Codex review: a reason that says --check made a fix a check, which skips the limit
        assert (spawn(cmd) or {"check": True})["check"] is False, f"--check in a reason is no flag: {cmd}"
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
