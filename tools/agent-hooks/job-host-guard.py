#!/usr/bin/env python3
"""PreToolUse check (Claude Code and Codex): a recurring job about to land on 회사맥 is asked once where it should run.

Company jobs do not belong on 회사맥, and a memory rule saying so failed twice in October. 회사맥 sleeps with its lid closed and
goes offline on the commute, so such a job stops with nobody told. A hook cannot know where a job belongs, so it forces the
judgment: the first launchd, cron or watch-job change of a target in a session is refused with the question. Repeating the
call passes, except in a job tab nobody has typed in: it stops there and leaves the question on the board.

Reads (`launchctl list`, `crontab -l`) and commands sent to another machine (`ssh server 'launchctl ...'`) are not asked.
It makes a session stop and think; it is no wall: a job installed from inside a script file or through a variable passes.
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
# Nobody reads a job tab, so a question it can answer by repeating itself is no stop (요유 2026-11-01, api#975).
JOB_ASK = ("job-host: 자동 세션은 회사맥의 정기 잡을 올리거나 바꾸지 않습니다. 같은 호출을 반복해도 통과하지 않습니다. 하던 일은 여기서 멈추고, "
           "어떤 잡을 왜 어느 머신에 둘지 board.sh comment로 남긴 뒤 owner를 yoyoo로 넘기세요. Claude 탭은 요유가 직접 입력하면 풀립니다.")
NAMES = re.compile(r"launchctl|crontab|install-launchd|LaunchAgents")
HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?([^\n]*)\n(.*?)\n[ \t]*\1\b", re.S)
QUOTED = re.compile(r"""'[^']*'|"(?:[^"\\]|\\.)*\"""")
AT_COMMAND = (r"(?:^|[;&|({\n`])\s*(?:(?:[A-Za-z_]\w*=\S*|sudo|command|exec|env|nohup|time|xargs|if|elif|while|until|then|do|else|!)"
              r"(?:\s+-\S+)*\s+)*")
LOCAL_SHELL = re.compile(AT_COMMAND + r"(?:\S*/)?(?:ba|z)?sh\s+-\w*c\s+$")
SHELL_STDIN = re.compile(AT_COMMAND + r"(?:\S*/)?(?:ba|z)?sh(?:\s+-\S+)*\s*$")
SUBSTITUTION = re.compile(r"(?<!\\)(?:\$\(([^()]*)\)|`([^`]*)`)")
# cp, mv and ln count only with LaunchAgents as the last argument: copying a job out of there is a read.
JOB = re.compile(AT_COMMAND + r"(?:\S*/)?(?:launchctl\s+(?:-\S+\s+)*(?:bootstrap|load|enable)\b"
                 r"|crontab\s+(?![^;&|\n]*(?<!\S)-[lr]\b)[^\s>]|(?:(?:ba|z)?sh\s+(?:\S*/)?)?install-launchd\b"
                 r"|tee\s[^;&|\n]*Library/LaunchAgents"
                 r"|(?:cp|mv|ln|install)\s[^;&|\n]*\s\S*Library/LaunchAgents\S*(?:\s+\d*>\s*\S+)*\s*(?:$|[;&|)\n]))"
                 r"|>>?\s*\S*Library/LaunchAgents")
JOB_FILE = re.compile(r"(?:^|/)Library/LaunchAgents/|(?:^|/)agent-board/watch/run\.sh$")
PATCH_PATH = re.compile(r"^\*\*\* (?:(?:Add|Update|Delete) File|Move to): (.+)$", re.M)


def run_here(command: str) -> str:
    """The command without what the shell here does not run: heredoc bodies and quoted text that is not a plain path.

    What stays: a quoted plain path, so `cp a "$HOME/Library/LaunchAgents/b"` is still seen; the text a local shell is given
    (`bash -c '...'`, `bash <<EOF`); and a substitution in double quotes, which runs here whatever the quotes go to.
    `ssh server 'a; b'` and `rg 'a|b'` are gone.
    """

    def heredoc(m):
        return f"{m.group(2)}\n{m.group(3)}\n" if SHELL_STDIN.search(m.string, 0, m.start()) else m.group(2)

    def quoted(m):
        text = m.group()
        if LOCAL_SHELL.search(m.string, 0, m.start()):
            return f"; {text[1:-1]} ;"
        ran = SUBSTITUTION.findall(text) if text[0] == '"' else []
        outer = SUBSTITUTION.sub("x", text) if ran else text
        return ("''" if re.search(r"[\s;&|()]", outer) else outer) + "".join(f" ; {a or b} ;" for a, b in ran)

    return QUOTED.sub(quoted, HEREDOC.sub(heredoc, command.replace("\\\n", " ")))


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
    cwd = str(payload.get("cwd") or "")
    full = (os.path.normpath(os.path.join(cwd, os.path.expanduser(path.strip()))) for path in paths if path.strip())
    return "\n".join(sorted(path for path in full if JOB_FILE.search(path))) or None


def job_tab(payload: dict, env: dict) -> bool:
    """A job tab nobody has typed in. The rule is dispatch/guard.sh's: 요유's own prompt lifts a dispatch tab for good and an
    ambient tab until the next round; an unreadable transcript lifts nothing. A Codex job tab records no such prompt, so it stays on."""
    ambient, marker = bool(env.get("MUNICE_AMBIENT")), "ambient-round" if env.get("MUNICE_AMBIENT") else "auto-dispatch"
    if not ambient and not env.get("MUNICE_AUTO_DISPATCH"):
        return False
    try:
        with open(str(payload.get("transcript_path") or ""), encoding="utf-8", errors="replace") as f:
            prompts = [line for line in f if '"origin":{"kind":"human"}' in line.replace(" ", "")]
    except OSError:
        return True
    return marker in prompts[-1] if ambient and prompts else all(marker in line for line in prompts)


def check(payload: dict, env: dict) -> str | None:
    """The question to refuse this call with, or None."""
    target = job_target(payload)
    session = re.sub(r"[^\w-]", "_", str(payload.get("session_id") or ""))
    if not target or not (session or env.get("MUNICE_AUTO_DISPATCH") or env.get("MUNICE_AMBIENT")):
        return None
    model = env.get("JOB_HOST_MODEL") or subprocess.run(["sysctl", "-n", "hw.model"], capture_output=True, text=True,
                                                         timeout=2).stdout.strip()
    if model != LAPTOP_MODEL:
        return None
    if job_tab(payload, env):
        return JOB_ASK
    if not session:
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
                 "nohup launchctl load /tmp/n.plist", "ls *.plist | xargs -n1 launchctl load", "launchctl \\\n  load /tmp/j.plist",
                 "bash <<'SCRIPT'\nlaunchctl load /tmp/l.plist\nSCRIPT", 'output="$(launchctl load /tmp/v.plist 2>&1)"',
                 'ssh server "echo $(launchctl load /tmp/w.plist)"', 'cp f "$HOME/Library/LaunchAgents/${NAME}.plist"',
                 'cp a.plist "$HOME/Library/LaunchAgents/$(whoami).plist"', 'ln -sf /tmp/job.plist "$HOME/Library/LaunchAgents/$(hostname).plist"',
                 'output="`launchctl load /tmp/bt.plist 2>&1`"', "x=`launchctl load /tmp/bu.plist`"]
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
                        "cp ~/Library/LaunchAgents/a.plist ~/backup/LaunchAgents/a.plist", "cat a > /tmp/x/LaunchAgents/a.plist", "rg -n 'launchctl|crontab|install-launchd|LaunchAgents' /repo",
                        "ssh server 'true;install-launchd'", "ssh server bash <<'EOF'\nlaunchctl load /tmp/a\nEOF",
                        'printf "%s\\n" "\\$(launchctl load /tmp/local.plist)"', "echo '$(launchctl load /tmp/a)'"):
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
        for payload in ({**patch("job.plist"), "cwd": "/Users/y/Library/LaunchAgents"},
                        {**patch("run.sh"), "cwd": "/y/.agents/skills/agent-board/watch"}):
            assert check(payload, env) == ASK, f"a relative path counts from cwd: {payload}"
        first, both = "/Users/y/Library/LaunchAgents/a1.plist", "\n*** Add File: /Users/y/Library/LaunchAgents/b1.plist"
        assert check(patch(first), env) == ASK and check(patch(first), env) is None
        assert check(patch(first + both), env) == ASK, "a new target beside an answered one is asked"
        assert check(patch(first + both), env) is None, "and the same patch again passes"

        assert check(bash("launchctl load /tmp/d.plist", "s2"), env) == ASK, "another session is asked again"
        assert check(bash("launchctl load /tmp/e.plist", "s2"), env) == ASK, "another target is asked again"
        assert check(bash("launchctl load /tmp/f.plist", ""), env) is None, "no session id: nothing to key on"
        assert check(bash("launchctl load /tmp/g.plist"), {**env, "JOB_HOST_MODEL": "Mac14,7"}) is None, "other machines pass"
        assert check({"session_id": "s1", "tool_name": "Bash"}, env) is None, "no input: fail open"

        # 요유 2026-11-01 (api#975): nobody reads a job tab, so its repeat never passes until 요유 types there.
        def transcript(*prompts):
            f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", dir=d, delete=False)
            f.write("".join(json.dumps({"type": "user", "origin": {"kind": "human"}, "message": {"content": p}}) + "\n" for p in prompts))
            f.close()
            return f.name

        def job(command, marker_env, path):
            return check({**bash(command, "job"), "transcript_path": path}, {**env, marker_env: "1"})

        typed_only_by_job = transcript("[auto-dispatch] work on this item")
        for attempt in (1, 2, 3):
            assert job("launchctl load /tmp/job1.plist", "MUNICE_AUTO_DISPATCH", typed_only_by_job) == JOB_ASK, f"try {attempt} stops"
        assert job("launchctl load /tmp/job2.plist", "MUNICE_AUTO_DISPATCH", "/nonexistent/x.jsonl") == JOB_ASK, "no transcript never lifts it"
        taken_over = transcript("[auto-dispatch] work on this item", "이 잡은 회사맥에 올려")
        assert job("launchctl load /tmp/job3.plist", "MUNICE_AUTO_DISPATCH", taken_over) == ASK, "요유 typed: asked like any session"
        assert job("launchctl load /tmp/job3.plist", "MUNICE_AUTO_DISPATCH", taken_over) is None, "and the repeat passes"
        rounds = transcript("[ambient-round] first", "직접 입력", "[ambient-round] next")
        assert job("launchctl load /tmp/job4.plist", "MUNICE_AMBIENT", rounds) == JOB_ASK, "an ambient round stops again after 요유 typed"
        typed = transcript("[ambient-round] first", "직접 입력")
        assert job("launchctl load /tmp/job5.plist", "MUNICE_AMBIENT", typed) == ASK, "요유 typed in this round"
        assert check({**bash("launchctl load /tmp/job6.plist", "job"), "transcript_path": typed_only_by_job},
                     {**env, "JOB_HOST_MODEL": "Mac14,7", "MUNICE_AUTO_DISPATCH": "1"}) is None, "other machines pass"
        assert job("launchctl list", "MUNICE_AUTO_DISPATCH", typed_only_by_job) is None, "a read is no job change"

        # Through the real entry point, the way both tools call it.
        guard = os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "claude", "hooks", "gcp-account-guard.py")

        def run(payload, **extra):
            return subprocess.run([sys.executable, "-I", guard], input=json.dumps(payload), capture_output=True, text=True,
                                  env={**os.environ, **env, "PANE_FIT_DIR": d, **extra}, timeout=10)

        for attempt in (1, 2):
            out = run({**bash("launchctl load /tmp/entry-job.plist", "job"), "transcript_path": typed_only_by_job}, MUNICE_AUTO_DISPATCH="1")
            assert json.loads(out.stdout)["hookSpecificOutput"]["permissionDecisionReason"] == JOB_ASK, f"entry try {attempt}: {out.stdout!r}"

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
