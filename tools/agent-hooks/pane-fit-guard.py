#!/usr/bin/env python3
"""PreToolUse check (Claude Code and Codex): before a session starts work, make it decide whether the work belongs in a pane.

요유 wants anything over a few minutes in its own herdr pane, chosen without being told. The memory rule failed three times on
10/30 and once on 10/31: sessions judged a job "small" or never judged at all. A hook cannot know how long a job will take, so
it forces the judgment: at the first working tool after each message from 요유, or at the first tool past 5 minutes, it refuses
the call once with the question. Repeating the call passes. In the last 7 days 166 of 1,076 turns reached a working tool
(24 a day, before the exemptions below) and 64% of those ran 5 minutes or more, so the stop is rarely wasted.

Not asked: panes `herdr agent start` opened (they are the handed-over work; herdr's agent name tells them apart with nothing to
remember), job tabs, headless runs, subagents, and sessions outside herdr. A pane herdr cannot find is not asked either.

pane-label-nudge.sh arms it on every message, by touching ~/.local/state/pane-fit/<session id>; asking removes that file.
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
GIT_WORK = re.compile(r"\bgit\b[^|;&\n]*\s(?:commit|push)\b|\bgh pr (?:create|merge)\b")
AT_COMMAND = r"(?:^|[;&|(\n])\s*(?:[A-Za-z_]\w*=\S*\s+)*"
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


def hand_opened(payload: dict, env) -> bool:
    """True for a session 요유 started by hand. A moved pane keeps an old HERDR_PANE_ID, so the session id is tried too."""
    herdr = env.get("HERDR_BIN_PATH") or "herdr"
    out = subprocess.run([herdr, "agent", "list"], capture_output=True, text=True, timeout=2).stdout
    for agent in json.loads(out)["result"]["agents"]:
        session = (agent.get("agent_session") or {}).get("value")
        if session == payload.get("session_id") or agent.get("pane_id") == env.get("HERDR_PANE_ID"):
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

        listing({"pane_id": "w1:p1", "agent_session": {"value": "s1"}})
        armed()
        assert check(p(), env) == START, "first work after a message is asked"
        assert check(p(), env) is None, "the repeat passes"
        assert check({**p(), "session_id": ""}, env) is None, "no session id: nothing to key on"

        for payload in (p("Write", file_path="/repo/a.py"), p("MultiEdit", file_path="/repo/a.py"),
                        p("NotebookEdit", notebook_path="/repo/a.ipynb"), p("Agent", subagent_type="general-purpose"),
                        p("Agent", prompt="x"), p("Task", subagent_type="fork"), p("Workflow", script="x"),
                        p("Bash", command="git commit -m x"), p("Bash", command="git -C /repo push origin main"),
                        p("Bash", command="gh pr create --fill"), p("Bash", command="ls && gh pr merge 1"),
                        p("Bash", command="npm test"), p("Bash", command="npm run build"), p("Bash", command="uv run pytest -q"),
                        p("Bash", command="gcloud run deploy api --image x"), p("Bash", command="terraform apply"),
                        p("Bash", command="bash deploy-release.sh worker"), p("Bash", command="./gradlew assemble"),
                        p("apply_patch", command="*** Begin Patch\n*** Update File: /repo/a.py\n*** End Patch")):
            armed()
            assert check(payload, env) == START, payload
        for payload in (p("Read", file_path="/repo/a.py"), p("Bash", command="git status"), p("Bash", command="git log -3"),
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
