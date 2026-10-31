#!/usr/bin/env python3
"""PreToolUse check (Claude Code and Codex): a recurring job about to land on 회사맥 is asked once where it should run.

Company jobs do not belong on 회사맥, and a memory rule saying so failed twice in October. 회사맥 sleeps with its lid closed and
goes offline on the commute, so such a job stops with nobody told. A hook cannot know where a job belongs, so it forces the
judgment: the first launchd, cron or watch-job change of a target in a session is refused with the question. Repeating the
call passes.

Reads (`launchctl list`, `crontab -l`) and commands sent to another machine (`ssh server 'launchctl ...'`) are not asked.
gcp-account-guard.py calls check(), for the reason pane-fit-guard.py gives: that hook is already registered in both tools with
the widest matcher, so this reaches open sessions and needs no new approval in Codex. Fails open.

    job-host-guard.py --selftest
"""
from __future__ import annotations  # a hook whose python3 is macOS's 3.9 would crash and let everything through

import hashlib
import os
import re
import subprocess
import sys

sys.dont_write_bytecode = True  # no __pycache__ in the dotfiles at every tool call
EDITORS = {"Edit", "Write", "MultiEdit"}
LAPTOP_MODEL = "Mac16,8"  # ponytail: a new 회사맥 silently turns this off, update the model then
ASK = ("job-host: 회사맥에 정기 잡을 올리거나 바꾸기 직전입니다. 회사맥은 덮개를 닫거나 출퇴근하면 멈추므로, 계속 돌아야 하는 회사 잡은 "
       "회사서버맥의 launchd에 둡니다. 회사맥에는 요유의 세션이나 회사맥 도구가 꼭 필요한 잡만 둡니다. "
       "요유가 회사맥을 지정했거나 이유가 분명하면 같은 호출을 그대로 다시 실행하고, 보고에 어느 머신에 왜 두었는지 적으세요. "
       "아니면 이 호출을 버리고 회사서버맥에 올리세요. 어디에 둘지 판단할 수 없으면 요유에게 물으세요.")
NAMES = re.compile(r"launchctl|crontab|install-launchd|LaunchAgents")
HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?([^\n]*)\n.*?\n[ \t]*\1\b", re.S)
QUOTED = re.compile(r"""'[^']*'|"(?:[^"\\]|\\.)*\"""")
AT_COMMAND = (r"(?:^|[;&|({\n])\s*(?:(?:[A-Za-z_]\w*=\S*|sudo|command|exec|env|nohup|time|xargs|if|elif|while|until|then|do|else|!)"
              r"(?:\s+-\S+)*\s+)*")
LOCAL_SHELL = re.compile(AT_COMMAND + r"(?:\S*/)?(?:ba|z)?sh\s+-\w*c\s+$")
# cp, mv and ln count only with LaunchAgents as the last argument: copying a job out of there is a read.
JOB = re.compile(AT_COMMAND + r"(?:\S*/)?(?:launchctl\s+(?:-\S+\s+)*(?:bootstrap|load|enable)\b"
                 r"|crontab\s+(?![^;&|\n]*(?<!\S)-[lr]\b)[^\s>]|(?:(?:ba|z)?sh\s+(?:\S*/)?)?install-launchd\b"
                 r"|tee\s[^;&|\n]*Library/LaunchAgents"
                 r"|(?:cp|mv|ln|install)\s[^;&|\n]*\s\S*Library/LaunchAgents\S*(?:\s+\d*>\s*\S+)*\s*(?:$|[;&|)\n]))"
                 r"|>>?\s*\S*Library/LaunchAgents")
JOB_FILE = re.compile(r"(?:^|/)Library/LaunchAgents/|(?:^|/)agent-board/watch/run\.sh$")
PATCH_PATH = re.compile(r"^\*\*\* (?:(?:Add|Update|Delete) File|Move to): (.+)$", re.M)


def run_here(command: str) -> str:
    """The command without what the shell here does not run: heredoc bodies and quoted arguments with a space in them.

    A quoted path with no space stays, so `cp a "$HOME/Library/LaunchAgents/b"` is still seen, and so does the text of a local
    `bash -c '...'`. `ssh server 'a; b'` is gone.
    """
    command = HEREDOC.sub(r"\2", command.replace("\\\n", " "))

    def quoted(m):
        if LOCAL_SHELL.search(command, 0, m.start()):
            return "; " + m.group()[1:-1]
        return "''" if re.search(r"\s", m.group()) else m.group()

    return QUOTED.sub(quoted, command)


def job_target(payload: dict) -> str | None:
    """What the call would change, or None. Must stay cheap: every tool call lands here."""
    tool, inp = payload.get("tool_name"), payload.get("tool_input") or {}
    if tool == "Bash":
        command = str(inp.get("command") or "")
        return command if NAMES.search(command) and JOB.search(run_here(command)) else None
    if tool == "apply_patch":
        paths = PATCH_PATH.findall(str(inp.get("command") or inp.get("input") or inp.get("patch") or ""))
    else:
        paths = [str(inp.get("file_path") or "")] if tool in EDITORS else []
    return next((path for path in map(str.strip, paths) if JOB_FILE.search(path)), None)


def check(payload: dict, env: dict) -> str | None:
    """The question to refuse this call with, or None."""
    target = job_target(payload)
    session = re.sub(r"[^\w-]", "_", str(payload.get("session_id") or ""))
    if not target or not session:
        return None
    model = env.get("JOB_HOST_MODEL") or subprocess.run(["sysctl", "-n", "hw.model"], capture_output=True, text=True,
                                                         timeout=2).stdout.strip()
    if model != LAPTOP_MODEL:
        return None
    folder = env.get("JOB_HOST_DIR") or os.path.expanduser("~/.local/state/job-host")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{session}-{hashlib.sha1(target.encode()).hexdigest()[:12]}")
    if os.path.exists(path):
        return None
    open(path, "w").close()
    return ASK


def selftest():
    import json
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        env = {"JOB_HOST_DIR": d, "JOB_HOST_MODEL": LAPTOP_MODEL}

        def bash(command, session="s1"):
            return {"session_id": session, "tool_name": "Bash", "tool_input": {"command": command}}

        def edit(path, tool="Write"):
            return {"session_id": "s1", "tool_name": tool, "tool_input": {"file_path": path}}

        asked = ["launchctl load ~/Library/LaunchAgents/com.yoyoo.x.plist", "launchctl bootstrap gui/501 /tmp/a.plist",
                 "launchctl enable gui/501/com.yoyoo.x", "sudo launchctl load /tmp/b.plist", "ls && launchctl load /tmp/c.plist",
                 "crontab /tmp/jobs", "crontab -e", "bash /x/ambient/install-launchd.sh", "cp a.plist ~/Library/LaunchAgents/",
                 "ln -s /x/a.plist /Users/y/Library/LaunchAgents/a.plist", "cat a > ~/Library/LaunchAgents/a.plist",
                 "(crontab -l; echo '0 9 * * * x') | crontab -", 'for f in a b; do launchctl load "$f"; done',
                 "cat > ~/Library/LaunchAgents/h.plist <<'EOF'\n<plist/>\nEOF", "echo x | tee ~/Library/LaunchAgents/t.plist",
                 'cp a.plist "$HOME/Library/LaunchAgents/q.plist" 2>&1 | tail -1', "env A=1 sudo -n launchctl load /tmp/z.plist",
                 "cat <<EOF | crontab -\n0 9 * * * x\nEOF", "cat <<'EOF' > ~/Library/LaunchAgents/o.plist\n<plist/>\nEOF",
                 "cat <<'EOF' | tee ~/Library/LaunchAgents/u.plist\n<plist/>\nEOF", "cp b.plist ~/Library/LaunchAgents",
                 'ln -sf /x/c.plist "$HOME/Library/LaunchAgents"', "cp d ~/Library/LaunchAgents/e 2> /dev/null",
                 "bash -c 'launchctl load /tmp/k.plist'", "zsh -lc 'crontab /tmp/jobs2'",
                 "sudo sh -c 'cat > /Library/LaunchAgents/r.plist'", "if ! launchctl load /tmp/i.plist; then echo x; fi",
                 "nohup launchctl load /tmp/n.plist", "ls *.plist | xargs -n1 launchctl load", "launchctl \\\n  load /tmp/j.plist"]
        for command in asked:
            assert check(bash(command), env) == ASK, command
            assert check(bash(command), env) is None, f"the repeat passes: {command}"
        for payload in (edit("/Users/y/Library/LaunchAgents/com.yoyoo.y.plist"),
                        edit("/Users/y/Library/LaunchAgents/com.yoyoo.z.plist", "Edit"),
                        edit("/Users/y/work/munice/.agents/skills/agent-board/watch/run.sh", "Edit"),
                        edit("/Users/y/work/munice/munice-claude/.agents/skills/agent-board/watch/run.sh", "MultiEdit")):
            assert check(payload, env) == ASK, payload
            assert check(payload, env) is None, f"the repeat passes: {payload}"

        for command in ("launchctl list", "launchctl print gui/501/com.yoyoo.x", "launchctl kickstart gui/501/com.yoyoo.x",
                        "crontab -l", "ssh server 'launchctl load /tmp/a.plist'", "ssh server launchctl load /tmp/a",
                        "ls ~/Library/LaunchAgents/", "cat ~/Library/LaunchAgents/a.plist", "grep -l x ~/Library/LaunchAgents/*.plist",
                        "echo 'launchctl load x'", "git log --oneline", "ssh server 'launchctl unload a; launchctl load a'",
                        "ssh server bash -s <<'EOF'\nlaunchctl load /tmp/a\nEOF", "cp ~/Library/LaunchAgents/a.plist /tmp/a.plist",
                        'git commit -m "fix: launchctl load; crontab x"', 'rg "launchctl load" tools', "crontab -l | grep x",
                        "scp a.plist server:~/Library/LaunchAgents/", "launchctl list | grep yoyoo", "command -v crontab >/dev/null 2>&1",
                        "crontab -u yoyoo -l", "ssh server \\\n  launchctl load /tmp/a", "ssh server bash -c 'launchctl load /tmp/a'",
                        "cp ~/Library/LaunchAgents/a.plist ~/backup/LaunchAgents/a.plist", "cat a > /tmp/x/LaunchAgents/a.plist"):
            assert check(bash(command), env) is None, command
        for payload in (edit("/repo/a.py"), edit("/Users/y/Library/LaunchAgents/a.plist", "Read"),
                        edit("/repo/watch/run.sh.bak")):
            assert check(payload, env) is None, payload

        def patch(path, key="command"):
            return {"session_id": "s1", "tool_name": "apply_patch",
                    "tool_input": {key: f"*** Begin Patch\n*** Update File: {path}\n@@\n-a\n+b\n*** End Patch"}}

        for payload in (patch("/Users/y/Library/LaunchAgents/p.plist"), patch("/x/.agents/skills/agent-board/watch/run.sh", "input"),
                        patch(".agents/skills/agent-board/watch/run.sh", "patch")):
            assert check(payload, env) == ASK, payload
            assert check(payload, env) is None, f"the repeat passes: {payload}"
        assert check(patch("/repo/a.py"), env) is None, "another patched file"
        moved = patch("/tmp/m.plist\n*** Move to: /Users/y/Library/LaunchAgents/m.plist")
        assert check(moved, env) == ASK, "a patch that moves a file there"

        assert check(bash("launchctl load /tmp/d.plist", "s2"), env) == ASK, "another session is asked again"
        assert check(bash("launchctl load /tmp/e.plist", "s2"), env) == ASK, "another target is asked again"
        assert check(bash("launchctl load /tmp/f.plist", ""), env) is None, "no session id: nothing to key on"
        assert check(bash("launchctl load /tmp/g.plist"), {**env, "JOB_HOST_MODEL": "Mac14,7"}) is None, "other machines pass"
        assert check({"session_id": "s1", "tool_name": "Bash"}, env) is None, "no input: fail open"

        # Through the real entry point, the way both tools call it.
        guard = os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "claude", "hooks", "gcp-account-guard.py")

        def run(payload, **extra):
            return subprocess.run([sys.executable, "-I", guard], input=json.dumps(payload), capture_output=True, text=True,
                                  env={**os.environ, **env, "PANE_FIT_DIR": d, **extra}, timeout=10)

        out = run(bash("launchctl load /tmp/entry.plist"))
        assert out.returncode == 0, out.stderr
        decision = json.loads(out.stdout)["hookSpecificOutput"]
        assert decision["permissionDecision"] == "deny" and decision["permissionDecisionReason"] == ASK, out.stdout
        assert run(bash("launchctl load /tmp/entry.plist")).stdout == "", "the repeat passes through the guard"
        not_a_folder = os.path.join(d, "not-a-folder")
        open(not_a_folder, "w").close()
        broken = run(bash("launchctl load /tmp/broken.plist"), JOB_HOST_DIR=not_a_folder)
        assert (broken.returncode, broken.stdout) == (0, ""), f"a crash in this check passes: {broken}"
        refused = run(bash(" ".join(["gcloud", "config", "set", "account", "a@b.c"])), JOB_HOST_DIR=not_a_folder)
        assert "gcp-account-guard" in refused.stdout, f"and leaves the GCP verdict alone: {refused}"
    print("selftest ok")


if __name__ == "__main__" and "--selftest" in sys.argv:
    selftest()
