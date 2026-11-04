#!/usr/bin/env python3
"""PreToolUse check (Claude Code and Codex): before a session starts work, make it decide whether the work belongs in a pane.

요유 wants anything over a few minutes in its own herdr pane, chosen without being told. The memory rule failed three times on
10/30 and once on 10/31: sessions judged a job "small" or never judged at all. A hook cannot know how long a job will take, so
it forces the judgment: at the first working tool after each message from 요유, or at the first tool past 5 minutes, it refuses
the call once with the question. Repeating the call passes. In the last 7 days 166 of 1,076 turns reached a working tool
(24 a day, before the exemptions below) and 64% of those ran 5 minutes or more, so the stop is rarely wasted.

Not asked: panes `herdr agent start` opened (they are the handed-over work; herdr's agent name tells them apart with nothing to
remember), job tabs, headless runs, subagents, and sessions outside herdr. A pane herdr cannot find is not asked either.

A pane that hands checks out (herdr_peers.delegates_checks: the main pane today, 요유 2026-11-05) is asked a different question: at
the first call of any kind but its own chores (stays_here), since a lookup there holds up 요유's next words as much as work does.

pane-label-nudge.sh arms it on every message, by writing ~/.local/state/pane-fit/<session id>, with DELEGATES in it for such a pane
(which is armed by relayed messages too: 요유's Slack words reach it by pane-tell); asking removes that file. An empty file is the
ordinary question, so a main-pane file that is missing or stale changes nothing here.
gcp-account-guard.py calls check(): that hook is already registered in both tools with the widest matcher. A new entry would
wait for 요유's approval in Codex and never reach open sessions. Fails open.

    pane-fit-guard.py --selftest
"""
from __future__ import annotations  # a hook whose python3 is macOS's 3.9 would crash and let everything through

import json
import os
import re
import subprocess
import sys
import time

sys.dont_write_bytecode = True  # no __pycache__ in the dotfiles at every tool call
WAIT = 300
EDITORS = {"Edit", "Write", "MultiEdit", "NotebookEdit", "apply_patch"}
READ_ONLY_AGENTS = {"Explore", "researcher", "code-reviewer", "claude-code-guide", "Plan", "statusline-setup"}
NOTES = re.compile(r"/scratchpad/|/\.claude/projects/[^/]+/memory/|^/private/tmp/|^/tmp/|/explain/|/work-logs/")
AT_COMMAND = r"(?:^|[;&|(\n])\s*(?:[A-Za-z_]\w*=\S*\s+)*"
GIT_WORK = re.compile(AT_COMMAND + r"(?:(?:(?:command|exec|env)\s+)?(?:\S*/)?git(?:\s+-\S+(?:\s+(?:\"[^\"]*\"|'[^']*'|[^\s-]\S*))?)*"
                      r"\s+(?:commit|push)\b(?!-)|gh pr (?:create|merge)\b)")
# PreToolUse runs once before a long foreground command, so the 5-minute check never sees it: ask at the start.
LONG_RUN = re.compile(AT_COMMAND + r"(?:(?:uv run|npx|bash|sh)\s+)?(?:\S*/)?(?:npm (?:run )?(?:test|build)|yarn (?:test|build)|pytest"
                      r"|vitest|jest|docker build|terraform (?:plan|apply)|gradlew|xcodebuild|deploy-release|gcloud\b[^|;&\n]*\bdeploy)\b")
SPAWNS = re.compile(AT_COMMAND + r"(?:\S*/)?pane-spawn\b")
PATCH_PATH = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.M)
HOW = ("이 호출을 버리고 `pane-spawn \"<작업 이름>\" \"<짧은 지시>\"`로 넘기세요(지시에는 마지막 보고를 `남은 것` 줄로 끝내라고만 적습니다). "
       "그렇지 않으면 같은 호출을 그대로 다시 실행하세요. 이 질문은 요유 메시지마다 한 번만 나옵니다.")
START = ("pane-fit: 이 요청의 일을 시작하기 직전입니다. 몇 분 넘게 걸릴 일이면 넘깁니다. PR·리뷰·테스트·머지가 따르면 파일이 한 줄이어도, "
         "요유가 보거나 고칠 결과물이어도, 서로 독립인 일이 여럿이어도, 이 세션이 닫혀도 이어져야 해도 해당하고, 애매하면 넘깁니다. " + HOW)
LATE = "pane-fit: 이 요청을 시작한 지 5분이 넘었습니다. 남은 일이 몇 분 더 걸리면 지금 넘깁니다. " + HOW
# 요유 2026-11-05 (api#1002): 요유's next words wait behind the main pane's turn, so there even a lookup goes to a pane. A hook cannot
# tell a one-line answer or a step that needs `prod 허용` from any other call, so it asks once a message, and the repeat passes.
DELEGATES = "delegates"  # what pane-label-nudge.sh writes into the armed file when herdr_peers.delegates_checks says yes
DELEGATE_ASK = ("pane-fit: 이 칸은 일을 나눠 주기만 합니다. 요유의 다음 말을 바로 받아야 하니, 한 줄로 답할 수 없는 조회도 `pane-spawn \"<작업 이름>\" \"<짧은 지시>\"`로 넘기고, "
                "하위 칸의 결과 확인은 `pane-spawn --check`로 새 확인 칸에 맡기세요(지시에는 마지막 보고를 `남은 것` 줄로 끝내라고만 적습니다). "
                "이 칸에 남는 일은 한 줄 답, `prod 허용`·`staging 허용`이 필요한 단계, 짧은 보고 읽기, pane-spawn 실행뿐입니다. "
                "이 호출이 그중 하나면 같은 호출을 그대로 다시 실행하세요. 이 질문은 메시지마다 한 번만 나옵니다.")
# What a pane that hands work out does all day. Asking here would stop its handing out and its fresh-start reads, not a lookup.
OWN_TOOLS = {"ToolSearch", "Skill", "TaskCreate", "TaskUpdate", "TaskList", "TaskGet", "TaskStop", "ScheduleWakeup",
             "AskUserQuestion", "mcp__task-progress__progress", "update_plan"}
OWN_FILES = re.compile(r"/\.local/state/(?:pane-reports|orchestrator)/|/skills/main-orchestrator/SKILL\.md$")
# Each may sit in an `if` or an `out=$(…)`: a stuck pane is read and answered that way (seen 2026-11-05).
LEAD_IN = r"^\s*(?:(?:if|then|elif|else|!)\s+)*"
OWN_COMMAND = re.compile(LEAD_IN + r"(?:\w+=\$\(\s*)?(?:(?:\S*/)?(?:cd|pane-label|pane-tell|pane-spawn|herdr|board\.sh|decision-log|handoff|launchctl)\b"
                         r"|python3\s+\S*main-channel\.py\b|(?:cat|tail|head|grep|rg|awk|sed)\s.*/\.local/state/(?:pane-reports|orchestrator)/)")
NEUTRAL = re.compile(LEAD_IN + r"(?:echo|printf|sleep|true|fi)\b")  # prints or waits: fine beside a chore, no chore by itself
QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")  # a message's words are no commands
STAGES = re.compile(r"&&|\|\||;|\n")


def state_file(payload: dict, env) -> str | None:
    session = re.sub(r"[^\w-]", "_", str(payload.get("session_id") or ""))
    folder = env.get("PANE_FIT_DIR") or os.path.expanduser("~/.local/state/pane-fit")
    return os.path.join(folder, session) if session else None


def starts_work(payload: dict) -> bool:
    tool, inp = payload.get("tool_name"), payload.get("tool_input") or {}
    if tool in EDITORS:
        patch = str(inp.get("command") or inp.get("input") or inp.get("patch") or "")
        paths = PATCH_PATH.findall(patch) if tool == "apply_patch" else [str(inp.get("file_path") or inp.get("notebook_path") or "")]
        return not all(NOTES.search(os.path.expanduser(p.strip())) for p in paths if p.strip())
    if tool in ("Agent", "Task"):
        return inp.get("subagent_type") not in READ_ONLY_AGENTS
    if tool == "Workflow":
        return True
    command = str(inp.get("command") or "")
    return tool == "Bash" and bool(GIT_WORK.search(command) or LONG_RUN.search(command))


def stays_here(payload: dict) -> bool:
    """A pane that hands work out does this without being asked. ponytail: a word list and a regex reading of shell; a miss costs one
    repeated call, once a message."""
    tool, inp = payload.get("tool_name"), payload.get("tool_input") or {}
    if tool == "Bash":
        stages = (stage.split("|")[0] for stage in STAGES.split(QUOTED.sub("''", str(inp.get("command") or ""))))
        real = [stage for stage in stages if stage.strip() and not NEUTRAL.search(stage)]
        return bool(real) and all(OWN_COMMAND.search(stage) for stage in real)
    if tool in EDITORS:
        return not starts_work(payload)
    return tool in OWN_TOOLS or (tool == "Read" and bool(OWN_FILES.search(str(inp.get("file_path") or ""))))


def hand_opened(payload: dict, env) -> bool:
    """True for a session 요유 started by hand. A moved pane keeps an old HERDR_PANE_ID, so the session id comes first.

    The pane id counts only for a pane whose session herdr does not know: a `codex exec` run inside a pane is not that pane.
    """
    herdr = env.get("HERDR_BIN_PATH") or "herdr"
    out = subprocess.run([herdr, "agent", "list"], capture_output=True, text=True, timeout=2).stdout
    for agent in json.loads(out)["result"]["agents"]:
        session = (agent.get("agent_session") or {}).get("value")
        if session == payload.get("session_id") or (not session and agent.get("pane_id") == env.get("HERDR_PANE_ID")):
            return not agent.get("name")
    return False


def check(payload: dict, env, now: float | None = None) -> str | None:
    """The question to refuse this call with, or None."""
    path = state_file(payload, env)
    if not path or not os.path.exists(path):  # the usual case, and it must stay cheap: every tool call lands here
        return None
    if (not env.get("HERDR_PANE_ID") or env.get("MUNICE_AUTO_DISPATCH") or env.get("MUNICE_AMBIENT")
            or env.get("CLAUDE_CODE_ENTRYPOINT", "").startswith("sdk") or payload.get("agent_id")):
        return None
    late = (time.time() if now is None else now) - os.path.getmtime(path) >= WAIT
    spawning = payload.get("tool_name") == "Bash" and bool(SPAWNS.search(str((payload.get("tool_input") or {}).get("command"))))
    with open(path, encoding="utf-8") as f:
        delegates = f.read().strip() == DELEGATES
    if delegates and not spawning:
        if stays_here(payload):
            return None
        os.unlink(path)
        return DELEGATE_ASK
    if not (late or spawning or starts_work(payload)):
        return None
    os.unlink(path)  # first, so a broken herdr call never asks twice
    if spawning or not hand_opened(payload, env):
        return None
    return START if starts_work(payload) else LATE


def selftest():
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        herdr = os.path.join(d, "herdr")
        agents = os.path.join(d, "agents.json")
        with open(herdr, "w", encoding="utf-8") as f:
            f.write(f'#!/bin/sh\ncat "{agents}"\n')
        os.chmod(herdr, 0o755)

        def listing(*rows):
            with open(agents, "w", encoding="utf-8") as f:
                json.dump({"result": {"agents": list(rows)}}, f)

        env = {"HERDR_PANE_ID": "w1:p1", "HERDR_BIN_PATH": herdr, "PANE_FIT_DIR": d, "CLAUDE_CODE_ENTRYPOINT": "cli"}

        def p(tool="Edit", **inp):
            return {"session_id": "s1", "tool_name": tool, "tool_input": inp or {"file_path": "/repo/a.py"}}

        def armed(age=0):
            path = state_file(p(), env)
            open(path, "w").close()
            os.utime(path, (time.time() - age, time.time() - age))

        # 요유 2026-11-05 (api#1002): the main pane only hands work out, lookups and checks too. pane-label-nudge.sh writes the mark.
        def armed_main(age=0):
            armed(age)
            with open(state_file(p(), env), "w", encoding="utf-8") as f:
                f.write(DELEGATES)
            os.utime(state_file(p(), env), (time.time() - age, time.time() - age))

        listing({"pane_id": "w1:p1", "name": "any-agent-name", "agent_session": {"value": "s1"}})  # however this pane was opened
        for payload in (p("Read", file_path="/repo/a.py"), p("Grep", pattern="x"), p("Glob", pattern="*"), p("WebFetch", url="https://x.dev"),
                        p("mcp__slack__slack_get_thread_replies", thread_ts="1"), p(), p("Bash", command="gh issue view 1"),
                        p("Bash", command="git log -3"), p("Bash", command="cat /repo/a.py"), p("Bash", command="herdr agent list; npm test"),
                        p("Bash", command="cat /x/note.md | herdr pane list"), p("Agent", subagent_type="Explore"),
                        p("Bash", command="rg pane-tell tools"), p("Bash", command='echo "herdr"'), p("Read", file_path="/repo/main-orchestrator/a.py"),
                        # Real work next to the chores' shell grammar. What only prints or waits settles nothing alone.
                        p("Bash", command="awk '{print}' /repo/a.py"), p("Bash", command="sed -n 1,5p /repo/a.py"),
                        p("Bash", command="awk '/.local/state/pane-reports/' /repo/a.py"), p("Bash", command="out=$(cat /repo/a.py)"),
                        p("Bash", command="out=$(npm test); echo \"$out\""), p("Bash", command="out=$(herdr agent list); npm test"),
                        p("Bash", command="if rg x /repo; then echo y; fi"), p("Bash", command="if npm test; then herdr agent list; fi"),
                        p("Bash", command="if true; then echo y; else echo n; fi"), p("Bash", command="sleep 3"), p("Bash", command="printf '%s' x; echo y"),
                        p("Bash", command="echo 'out=$(herdr agent read w1:p2)'"), p("Bash", command="tail -5 ~/.local/state/pane-reports/a.md; npm test")):
            armed_main()
            assert check(payload, env) == DELEGATE_ASK, f"the main pane is asked: {payload}"
            assert check(payload, env) is None, f"once a message, so a one-line answer or a prod step goes on: {payload}"
        armed_main()
        assert check(p("Read", file_path="/repo/a.py"), {**env, "HERDR_BIN_PATH": "/nonexistent/herdr"}) == DELEGATE_ASK, "no herdr call decides it"
        for payload in (p("Bash", command='pane-spawn --check "a" "b"'), p("Bash", command='pane-spawn "a" "b"')):
            armed_main()
            assert check(payload, env) is None, "handing out is the answer"
            assert not os.path.exists(state_file(p(), env)), "and it ends the question"
        for payload in (p("Bash", command="pane-tell workers/w1:p2 '답입니다'"), p("Bash", command="pane-label --free"),
                        p("Bash", command="herdr --session workers agent list"), p("Bash", command="herdr --session workers agent read w1:p2 | tail -5"),
                        p("Bash", command="/w/.agents/skills/agent-board/board.sh mine yoyoo"), p("Bash", command="decision-log kinds"),
                        p("Bash", command="python3 /w/.agents/skills/agent-board/main/main-channel.py post /tmp/a"),
                        p("Bash", command="handoff save /tmp/a"), p("Bash", command="launchctl print gui/501/com.yoyoo.main-channel | grep -m1 'state ='"),
                        p("Bash", command="cd /tmp && herdr agent list"), p("Bash", command="tail -40 ~/.local/state/pane-reports/2026-11-05.md"),
                        p("Bash", command="cat ~/.local/state/orchestrator/personal-decisions.md"),
                        p("Read", file_path="/Users/y/.local/state/pane-reports/2026-11-05.md"),
                        p("Read", file_path="/Users/y/.local/state/orchestrator/reuse-notes.md"),
                        p("Read", file_path="/Users/y/.claude/skills/main-orchestrator/SKILL.md"), p("ToolSearch", query="x"), p("Skill", skill="x"),
                        p("TaskUpdate", taskId="1"), p("mcp__task-progress__progress", done_steps=0),
                        p("Write", file_path="/private/tmp/x/scratchpad/a.md"), p("Edit", file_path="/Users/y/.claude/projects/-x/memory/a.md"),
                        # Word for word what the real main pane was refused on 2026-11-05: an entry of the pane-reports file, a stuck pane.
                        *(p("Bash", command=real) for real in (
                            "awk '/01:09:13/{f=1} f&&/^## .*0?1:[0-9][0-9]:[0-9][0-9]/&&!/01:09:13/{exit} f' /Users/y/.local/state/pane-reports/2026-11-05.md | cut -c1-700 | head -40",
                            "herdr agent list | jq -r '.result.agents[] | [.pane_id, .agent_status, .name // \"-\", .terminal_title] | @tsv' | cut -c1-90; echo ---; pane-tell --list | cut -c1-70; echo ---; herdr --session workers agent list | jq -r '.result.agents[] | [.pane_id, .agent_status] | @tsv'",
                            "out=$(herdr --session workers agent read w1:pQ 2>&1); if printf '%s' \"$out\" | grep -q 'possibly-empty variable path: \\$SP/\\*.log'; then herdr --session workers agent send-keys w1:pQ Enter; echo \"answered Yes\"; else echo \"dialog differs:\"; printf '%s' \"$out\" | tail -c 700; fi; sleep 3; herdr --session workers agent list | jq -r '.result.agents[] | select(.pane_id==\"w1:pQ\") | .agent_status'",
                        )),
                        p("Bash", command="sed -n 159,215p ~/.local/state/pane-reports/2026-11-05.md"), p("Bash", command="rg -n 01:28 ~/.local/state/pane-reports/2026-11-05.md"),
                        p("Bash", command="herdr --session workers pane send-keys w1:pQ Enter"), p("Bash", command="out=$(herdr --session workers agent read w1:pQ); echo \"$out\" | tail -5"),
                        p("Bash", command="if ! herdr agent get w1:p2 >/dev/null; then echo gone; fi")):
            armed_main()
            assert check(payload, env) is None, f"what stays in the main pane: {payload}"
            assert os.path.exists(state_file(p(), env)), f"and the question stays for the first lookup: {payload}"
        armed_main(WAIT + 1)
        assert check(p("Bash", command="pane-tell w1:p2 x"), env) is None, "its own chores are never late"
        assert check(p("Read", file_path="/repo/a.py"), env) == DELEGATE_ASK, "and the first lookup is asked at any age"
        for extra in ({"MUNICE_AUTO_DISPATCH": "1"}, {"MUNICE_AMBIENT": "1"}, {"CLAUDE_CODE_ENTRYPOINT": "sdk-cli"}):
            armed_main()
            assert check(p(), {**env, **extra}) is None, extra
        armed_main()
        assert check({**p(), "agent_id": "a1"}, env) is None, "a subagent of the main pane is not asked"
        for word in ("pane-spawn", "확인", "prod 허용", "staging 허용", "한 줄"):
            assert word in DELEGATE_ASK, f"the question names what stays or goes: {word}"
        os.unlink(state_file(p(), env))

        listing({"pane_id": "w1:p1", "agent_session": {"value": "s1"}})
        # Any other pane has no chores: the main pane's list changes nothing here, the clock and real work still ask.
        chore = "out=$(herdr --session workers agent read w1:pQ); awk '/x/' ~/.local/state/pane-reports/a.md"
        armed()
        assert check(p("Bash", command=chore), env) is None and os.path.exists(state_file(p(), env)), "not work, so not asked yet"
        armed(WAIT + 1)
        assert check(p("Bash", command=chore), env) == LATE, "and past 5 minutes it is asked as any tool is"
        armed()
        assert check(p("Bash", command="out=$(herdr agent list); git commit -m x"), env) == START, "real work beside it is asked"
        armed()
        assert check(p(), env) == START, "first work after a message is asked"
        assert check(p(), env) is None, "the repeat passes"
        assert check({**p(), "session_id": ""}, env) is None, "no session id: nothing to key on"

        for payload in (p("Write", file_path="/repo/a.py"), p("MultiEdit", file_path="/repo/a.py"),
                        p("NotebookEdit", notebook_path="/repo/a.ipynb"), p("Agent", subagent_type="general-purpose"),
                        p("Agent", prompt="x"), p("Task", subagent_type="fork"), p("Workflow", script="x"),
                        p("Bash", command="git commit -m x"), p("Bash", command="git -C /repo push origin main"),
                        p("Bash", command="command git push origin main"), p("Bash", command="/usr/bin/git commit -m fix"),
                        p("Bash", command='git -C "/tmp/my repo" commit -m fix'),
                        p("Bash", command="gh pr create --fill"), p("Bash", command="ls && gh pr merge 1"),
                        p("Bash", command="npm test"), p("Bash", command="npm run build"), p("Bash", command="uv run pytest -q"),
                        p("Bash", command="gcloud run deploy api --image x"), p("Bash", command="terraform apply"),
                        p("Bash", command="bash deploy-release.sh worker"), p("Bash", command="./gradlew assemble"),
                        p("apply_patch", command="*** Begin Patch\n*** Update File: /repo/a.py\n*** End Patch")):
            armed()
            assert check(payload, env) == START, payload
        for payload in (p("Read", file_path="/repo/a.py"), p("Bash", command="git status"), p("Bash", command="git log -3"),
                        p("Bash", command="git stash push"), p("Bash", command="git commit-graph write"),
                        p("Bash", command="git log -- push"), p("Bash", command="echo 'gh pr merge'"),
                        p("Agent", subagent_type="Explore"), p("Write", file_path="/private/tmp/x/scratchpad/a.md"),
                        p("Write", file_path="/Users/y/.claude/projects/-x/memory/a.md"),
                        p("Edit", file_path="/Users/y/explain/a.html"),
                        p("apply_patch", command="*** Begin Patch\n*** Add File: /tmp/a\n*** End Patch")):
            armed()
            assert check(payload, env) is None, payload
            assert os.path.exists(state_file(p(), env)), f"still armed for the real work: {payload}"

        armed(WAIT + 1)
        assert check(p("Read", file_path="/repo/a.py"), env) == LATE, "any tool past 5 minutes is asked"
        armed(WAIT + 1)
        assert check(p(), env) == START, "work wins over the clock"
        armed()
        assert check(p("Bash", command='pane-spawn "a" "b"'), env) is None, "handing over is the answer"
        assert not os.path.exists(state_file(p(), env)), "and it ends the question"
        for command in ("cat tools/agent-hooks/pane-spawn", "rg pane-spawn tools", "grep -n pytest notes.md"):
            armed()
            assert check(p("Bash", command=command), env) is None, command
            assert os.path.exists(state_file(p(), env)), f"reading about it hands nothing over: {command}"
        armed()
        assert check(p("Bash", command='git commit -m "fix pane-spawn"'), env) == START, "a word in a message hands nothing over"

        for extra in ({"MUNICE_AUTO_DISPATCH": "1"}, {"MUNICE_AMBIENT": "1"}, {"CLAUDE_CODE_ENTRYPOINT": "sdk-cli"},
                      {"HERDR_PANE_ID": ""}):
            armed()
            assert check(p(), {**env, **extra}) is None, extra
        armed()
        assert check({**p(), "agent_id": "a1"}, env) is None, "a subagent is not asked"
        assert os.path.exists(state_file(p(), env)), "and leaves the question for the main session"

        listing({"pane_id": "w1:p1", "name": "recurrence-review", "agent_session": {"value": "s1"}})
        armed()
        assert check(p(), env) is None, "a pane `agent start` opened is the handed-over work"
        listing({"pane_id": "w2:p9", "name": "moved", "agent_session": {"value": "s1"}})
        armed()
        assert check(p(), env) is None, "a moved pane is found by its session"
        listing({"pane_id": "w2:p9", "agent_session": {"value": "s1"}})
        armed()
        assert check(p(), env) == START, "a moved hand-opened pane is still asked"
        listing()
        armed()
        assert check(p(), env) is None, "herdr has no record: not asked"
        listing({"pane_id": "w1:p1", "agent_session": {"value": "parent"}})
        armed()
        assert check(p(), env) is None, "a `codex exec` inside a pane is not the session of that pane"
        listing({"pane_id": "w1:p1"})
        armed()
        assert check(p(), env) == START, "a pane herdr knows only by id is still asked"

        # Through the real entry point, the way both tools call it.
        guard = os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "claude", "hooks", "gcp-account-guard.py")

        def run(payload):
            return subprocess.run([sys.executable, "-I", guard], input=json.dumps(payload), capture_output=True, text=True,
                                  env={**os.environ, **env}, timeout=10)

        listing({"pane_id": "w1:p1", "agent_session": {"value": "s1"}})
        armed()
        out = run(p())
        assert out.returncode == 0, out.stderr
        assert out.stdout, "the guard must ask"
        decision = json.loads(out.stdout)["hookSpecificOutput"]
        assert decision["permissionDecision"] == "deny" and decision["permissionDecisionReason"] == START, out.stdout
        assert run(p()).stdout == "", "the repeat passes through the guard"
        armed_main()
        lookup = p("Read", file_path="/repo/a.py")
        out = run(lookup)
        assert json.loads(out.stdout)["hookSpecificOutput"]["permissionDecisionReason"] == DELEGATE_ASK, out.stdout
        assert run(lookup).stdout == "", "a lookup's repeat passes through the guard"
        armed_main()
        assert run(p("Bash", command="out=$(herdr --session workers agent read w1:pQ); echo \"$out\"")).stdout == "", "a chore passes through the guard"
        assert os.path.exists(state_file(p(), env)), "with the question kept for the first lookup"
        armed()
        refused = run(p("Bash", command=" ".join(["gcloud", "config", "set", "account", "a@b.c"])))
        assert "gcp-account-guard" in refused.stdout, refused.stdout
        assert os.path.exists(state_file(p(), env)), "a GCP refusal does not use up the question"
        with open(agents, "w", encoding="utf-8") as f:
            f.write("not json")
        armed()
        broken = run(p())
        assert (broken.returncode, broken.stdout) == (0, ""), f"a broken herdr passes: {broken}"
    print("selftest ok")


if __name__ == "__main__" and "--selftest" in sys.argv:
    selftest()
