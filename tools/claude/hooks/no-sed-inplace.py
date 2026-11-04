#!/usr/bin/env python3
"""PreToolUse guard: edit files with the Edit tool, not `sed -i` / `perl -i` in Bash.

Edit only changes text it has read and that matches exactly once, so it cannot hit the
wrong line or overwrite another session's change. The memory rule alone ("sed/awk 금지")
was broken 45 times in 30 days (2026-10-17), because bypass mode's own instructions allow
sed edits. Reading with `sed -n` stays allowed.

Also holds the other Bash ban that must work in Claude and Codex alike: agents open panes with `pane-spawn`, never
`herdr agent start`, `herdr pane split` or `herdr tab create`, so every new job gets its own named tab and a lead mark
(요유 2026-10-31). Typing an agent into a pane (`herdr pane run`, `send-text`) is refused too: such a pane has no herdr name, so
it reads as a session 요유 opened and never closes. A test that needs one uses `pane-temp`, which closes itself. It lives
here because Codex runs a new hook only after 요유 trusts it, and this command is already trusted. Jobs that run outside an
agent's Bash tool (the dispatch job) never reach this hook.

Recursive `rm` and `git push --no-verify` live here for the same reason. Claude's deny list is a list of spellings and Codex
does not read it, and a session whose `rm -rf` was refused went on with `rm -r -f` and then one file at a time.
Scripts a command runs and `trap` cleanups are not seen, so hooks and jobs that clean up after themselves keep working.
ponytail: `$(rm -r x)`, a heredoc fed to bash, `case` arms, function bodies, `find -exec sudo rm`, `find -delete`, `ssh host rm`
and `git -c core.hooksPath=` get past it. Add one when a session reaches for it; a determined caller beats any command check.

    no-sed-inplace.py --selftest
"""
from __future__ import annotations  # a hook whose python3 is macOS's 3.9 would crash and let everything through

import importlib.util
import json
import os
import re
import sys

# ponytail: reuse secret-print-guard's tested command parser instead of a second copy.
sys.dont_write_bytecode = True  # no __pycache__ in the dotfiles checkout
_spec = importlib.util.spec_from_file_location(
    "secret_print_guard", os.path.join(os.path.dirname(os.path.realpath(__file__)), "secret-print-guard.py"))
assert _spec and _spec.loader
_guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_guard)


# In-place flags. sed: a short cluster of no-argument switches ending in i (-i, -Ei, -i.bak) or
# --in-place. perl: the same, but -M/-e take arguments, so `perl -Mstrict` is not -i.
INPLACE = {
    "sed": re.compile(r"^(?:-[Enrsuz]*i|--in-place)"),
    "gsed": re.compile(r"^(?:-[Enrsuz]*i|--in-place)"),
    "perl": re.compile(r"^-[npaswcTtWXl]*i"),
}
LAUNCHERS = {"xargs", "-exec", "-execdir"}  # sed reached through `xargs sed` or `find -exec sed`
# A regex, unlike the sed check: `pane=$(herdr pane split ...)` is the usual form, and strip_captures would hide it.
# ponytail: only the ways people write it, a determined caller can always get past a regex (`$H tab create`).
HERDR_BIN = re.compile(r'"?\$(?:HERDR_BIN_PATH\b|\{HERDR_BIN_PATH[^}]*\})"?')
HERDR = r"(?:^|[;&|(`]|\$\()\s*(?:(?:then|do|else|command|env)\s+)*(?:[A-Za-z_]\w*=\S*\s+)*(?:\S*/)?herdr(?:\s+-\S+(?:\s+[^\s-]\S*)?)*"
SPAWN = re.compile(HERDR + r"\s+(?:agent\s+start|pane\s+split|tab\s+create)\b(?![^\n;|&]*--help)", re.M)
# An agent typed into a pane gets no herdr name, so nothing closes it. LAUNCH reads the raw command, since the agent word sits
# inside the quotes code_only blanks; TYPES is what makes it a real call and not an example in a commit message. A one-shot
# subcommand starts no session. In send-text a following word is a sentence to the agent already there ("claude 쪽 결과…").
# `cl` and `cla` are 요유's aliases for claude.
ONE_SHOT = r"(?!\s+(?:mcp|auth|login|logout|doctor|update|config|plugin|exec|-p|--print|--version|-v|--help|-h)\b)"
AGENT = r"""\s+\S+\s+['"]?(?:cd\s+\S+\s*(?:&&|;)\s*|[A-Za-z_]\w*=\S*\s+)*(?:exec\s+)?(?:\S*/)?(?:claude|codex|cla?)(?![\w-])"""
TYPES = re.compile(HERDR + r"\s+pane\s+(?:run|send-text)\b", re.M)
LAUNCH = re.compile(HERDR + r"\s+pane\s+(?:run" + AGENT + ONE_SHOT + "|send-text" + AGENT
                    + r"""(?=\s+-|\s*(?:$|['";&|]|resume\b))""" + ONE_SHOT + ")", re.M)
QUOTED = re.compile(r"""'[^']*'|"(?:[^"\\]|\\.)*\"""")
CAPTURE = re.compile(r"\$\([^)]*\)|`[^`]*`")
RECURSIVE = re.compile(r"^(?:-[a-zA-Z]*[rR][a-zA-Z]*|--r[a-z]*)$")  # -r, -R, a cluster (-rf, -vR), --recursive
SHELL_WORDS = {"then", "do", "else", "elif", "if", "while", "until", "!", "{", ""}  # "" is a lone `(` after name()
RUNNERS = {"env", "nohup", "nice", "timeout", "gtimeout", "xargs", "caffeinate"}  # run the words after them
NOT_COMMAND = re.compile(r"-|[A-Za-z_]\w*=|\{\}$|[\d.]+[smhd]?$")  # a runner's own words: options, A=1, {}, a count, a duration
SHELLS = {"bash", "sh", "zsh"}
WATCHED = {"rm", "git", "eval"} | SHELLS


def name(tok: str) -> str:
    return tok.lstrip("(").rsplit("/", 1)[-1]


def simple_commands(cmd: str, depth: int = 0):
    """Word lists of the commands `cmd` runs, with what `bash -c` and `eval` are handed read as commands too."""
    for seg in _guard.segments(_guard.strip_captures(_guard.HEREDOC.sub("<<heredoc", cmd))):
        w = _guard.words(seg)
        while w and name(w[0]) in SHELL_WORDS:
            w = _guard.words(w[1:])
        if w and name(w[0]) in RUNNERS:
            w = _guard.words(next((w[k:] for k in range(1, len(w)) if not NOT_COMMAND.match(w[k])), w))
        elif w and w[0].startswith("-"):  # `words` drops sudo and leaves `sudo -u root`'s options
            w = next((w[k:] for k, tok in enumerate(w) if name(tok) in WATCHED), w)
        if not w:
            continue
        yield w
        if depth >= 3:
            continue
        if name(w[0]) == "eval":
            yield from simple_commands(" ".join(w[1:]), depth + 1)
        elif name(w[0]) in SHELLS:
            for k, tok in enumerate(w[1:-1], 1):
                if re.fullmatch(r"-[a-zA-Z]*c[a-zA-Z]*", tok):
                    yield from simple_commands(w[k + 1], depth + 1)
                    break


def removes_recursively(w: list[str]) -> bool:
    for k, tok in enumerate(w):
        if name(tok) == "rm" and (k == 0 or w[k - 1] in LAUNCHERS):
            args = w[k + 1:]
            args = args[:args.index("--")] if "--" in args else args  # after `--` a word is a file name
            return any(RECURSIVE.match(a) for a in args)
    return False


def skips_push_hook(w: list[str]) -> bool:
    if name(w[0]) != "git":
        return False
    k = 1
    while k < len(w) and w[k].startswith("-"):
        k += 2 if w[k] in ("-C", "-c", "--git-dir", "--work-tree") else 1
    return k < len(w) and w[k] == "push" and "--no-verify" in w[k + 1:]


def code_only(cmd: str) -> str:
    """Heredocs and quoted strings are text, also across lines (a commit message); only a capture in double quotes runs."""
    return QUOTED.sub(lambda m: " ; ".join(CAPTURE.findall(m.group())) if m.group()[0] == '"' else "''",
                      _guard.HEREDOC.sub("<<heredoc", cmd))


def bash_problem(cmd: str) -> str | None:
    cmd = HERDR_BIN.sub("herdr", cmd)
    code = code_only(cmd)
    if SPAWN.search(code) or (TYPES.search(code) and LAUNCH.search(_guard.HEREDOC.sub("<<heredoc", cmd))):
        return ('pane-spawn: 칸을 직접 띄우지 말 것. `pane-spawn "<일 이름>" "<지시>"`로 띄울 것: 리드의 칸은 숨은 workers 서버에, 🫡 칸의 칸은 그 옆에 열고, '
                '호출한 칸을 👑로 표시하고(이미 🫡이면 그대로), 세션을 기다려 지시까지 전달함. 이 리드가 따라가지 않을 별개의 일만 `--new-tab`. '
                '요유가 연 세션처럼 이름 없는 칸이 꼭 필요한 시험은 `pane-temp "<이름>"`: 일이 끝나거나 1시간이 지나면 스스로 닫힘')
    visible = _guard.strip_captures(_guard.HEREDOC.sub("<<heredoc", cmd))  # heredoc bodies are text
    for seg in _guard.segments(visible):
        w = _guard.words(seg)
        for k, tok in enumerate(w):
            flag = INPLACE.get(tok.rsplit("/", 1)[-1])
            if not flag or (k and w[k - 1] not in LAUNCHERS):
                continue
            if any(flag.match(a) for a in w[k + 1:]):
                return ("no-sed-inplace: `sed -i`/`perl -i`로 파일을 고치지 말 것. Read로 읽은 뒤 Edit 도구로 고칠 것"
                        "(같은 치환을 한 파일 여러 곳에 하면 Edit의 replace_all). 읽기용 `sed -n`은 괜찮음")
    for w in simple_commands(cmd):
        if removes_recursively(w):
            return ("no-recursive-rm: 재귀 삭제(`rm -r`)는 막혀 있음. production 일이 아니라 명령 형식 문제라 멈추지 않아도 됨. "
                    "지워도 되는 임시 파일이나 다시 만들어지는 산출물이면 `trash <경로>`로 휴지통에 옮기고 하던 일을 그대로 이어 갈 것. "
                    "휴지통으로 옮길 수 없거나 지워도 되는지 모르면 형태를 바꿔 다시 실행하지 말고 그대로 보고할 것")
        if skips_push_hook(w):
            return "no-push-bypass: `git push --no-verify`는 막혀 있음. push 전 시험이 실패하면 고치거나 그대로 보고할 것"
    return None


def selftest():
    deny = [
        "sed -i '' 's/a/b/' file.txt",
        "sed -i.bak -e 's/a/b/' f",
        "sed -Ei 's/a/b/' f",
        "gsed --in-place 's/a/b/' f",
        "/usr/bin/sed -i '' 's/x/y/' f",
        "cd repo && sed -i '' 's/a/b/' a.ts",
        "find . -name '*.ts' -exec sed -i '' 's/a/b/' {} \\;",
        "rg -l foo | xargs sed -i '' 's/foo/bar/'",
        "perl -pi -e 's/a/b/' f",
        "perl -i.bak -pe 's/a/b/' f",
        "cd repo  # go there\nsed -i '' 's/a/b/' f",
        "sed \\\n  -i '' 's/a/b/' f",
    ]
    allow = [
        "sed -n '1,40p' file.txt",
        "sed 's/a/b/' in.txt > out.txt",
        "cat f | sed -e 's/-i/x/'",
        "perl -Mstrict -e 'print 1'",
        "git commit -m 'docs: never use sed -i here'",
        "echo 'sed -i is blocked'",
        "cat > notes.md <<'EOF'\nsed -i '' 's/a/b/' f\nEOF",
        "grep -n 'sed -i' AGENTS.md",
    ]
    for c in deny:
        assert bash_problem(c), f"should deny: {c}"
    for c in allow:
        assert bash_problem(c) is None, f"should allow: {c} -> {bash_problem(c)}"

    deny = [
        "herdr agent start tmp --kind claude --pane w1:p2",
        "herdr pane split --pane w1:p1 --direction right",
        "/opt/homebrew/bin/herdr pane split --current",
        "herdr --session work agent start tmp --kind codex --pane w1:p2",
        "new=$(herdr pane split --pane w1:p1 | jq -r .result.pane.pane_id)",
        'new="$(herdr pane split --pane w1:p1)"',
        "new=`herdr pane split --pane w1:p1`",
        "ls && herdr agent start tmp --kind claude --pane w1:p2",
        "FOO=1 herdr pane split --current",
        "herdr pane split --pane w1:p1\nherdr agent prompt w1:p2 hi",
        'tab=$(herdr tab create --workspace w5 --label x --no-focus)',
        "herdr tab create --cwd /tmp",
        """herdr pane run "$pane" 'claude --model haiku'""",
        'herdr pane run w5:pDC "claude --model haiku --dangerously-skip-permissions" >/dev/null 2>&1; sleep 8',
        'herdr pane run "$pane" "codex; echo CODEX_EXIT=\\$?"',
        'herdr pane run "$p" claude',
        'herdr pane run "$p" "cd /tmp && claude"',
        'herdr pane run "$p" "FOO=1 /opt/homebrew/bin/codex"',
        'herdr pane run "$p" "codex resume x"',
        'herdr pane send-text "$p" "claude --model haiku"',
        'herdr pane run w1:p2 "claude hello"',
        'herdr pane run w1:p2 "exec claude"',
        'herdr pane run "$p" "cd /tmp; claude"',
        'herdr pane run "$p" cl',
        'herdr pane run "$p" "cla --model x"',
        "if ok; then herdr tab create; fi",
        "for i in 1; do herdr tab create; done",
        "command herdr tab create",
        "env A=1 herdr tab create",
        '"$HERDR_BIN_PATH" tab create --label x',
        # still only pane-spawn opens panes, on any server
        "herdr --session workers agent start tmp --kind claude --pane w1:p2",
        "HERDR_SOCKET_PATH=/x/herdr.sock herdr tab create --label x",
    ]
    allow = [
        # the main pane answers a dialog in a workers pane
        "herdr --session workers agent send-keys w1:p2 Enter",
        'herdr --session workers pane send-text w1:p2 "1"',
        "herdr --session workers agent read w1:p2",
        "pane-spawn '설계' '지시'",
        "pane-temp --kind codex '시험'",
        'herdr pane send-text "$p" "claude-code 버전 확인해 주세요"',
        'herdr pane send-text "$p" "codex-review 돌려 주세요"',
        'herdr pane run "$p" "claude-online --version"',
        "herdr tab create --help | head",
        "herdr tab close w1:t2",
        'herdr pane run "$pane" "claude mcp list > /tmp/x"',
        'herdr pane run "$p" "claude -p hi"',
        'herdr pane run "$p" "npm test"',
        """herdr pane send-text "$c" '중간 글'""",
        'herdr pane send-text "$p" "/exit"',
        'herdr pane send-text "$p" "claude 쪽 결과입니다"',
        "herdr agent prompt w1:p2 'herdr pane run x claude'",
        'herdr pane run "$p" "codex exec hi"',
        'git commit -m "test coverage:\nherdr pane run w1:p2 claude\n"',
        "herdr agent prompt w1:p2 'herdr pane split 금지'",
        "herdr agent get w1:p2 | jq .",
        "herdr pane list",
        "herdr tab list",
        "herdr pane split --help",
        "herdr agent start --help | head",
        "grep -rn 'herdr agent start' tools",
        'git commit -m "feat: replace herdr pane split"',
        'git commit -m "guard: refuse direct\nherdr agent start and pane split\n"',
        'echo "it\'s fine; herdr pane split is banned"',
        "cat > notes.md <<'EOF'\nherdr pane split --current\nEOF",
        "bash /tmp/dispatch/run.sh",
    ]
    for c in deny:
        assert "pane-spawn" in (bash_problem(c) or ""), f"should deny: {c}"
    for c in allow:
        assert bash_problem(c) is None, f"should allow: {c} -> {bash_problem(c)}"

    deny = [
        "rm -r build",
        "rm -R build",
        "rm -rf build",
        "rm -fr build",
        "rm -Rf build",
        "rm -fR build",
        "rm -vr build",
        "rm -rfv build",
        "rm -r -f build",
        "rm -f -r build",
        "rm -v -r build",
        "rm --recursive build",
        "rm build -r",
        "/bin/rm -r build",
        "command rm -r build",
        "sudo rm -r build",
        "FOO=1 rm -r build",
        "env FOO=1 rm -r build",
        "nohup rm -r build",
        "timeout 5 rm -r build",
        "cd repo && rm -r build",
        "ls; rm -r build",
        "(rm -r build)",
        "if [ -d build ]; then rm -r build; fi",
        "for d in a b; do rm -r \"$d\"; done",
        "find . -name build -exec rm -r {} +",
        "find . -name build -exec rm -rf {} \\;",
        "ls | xargs rm -r",
        "ls | xargs -0 rm -rf",
        "ls | xargs -I{} rm -r {}",
        "bash -c 'rm -r build'",
        "sh -c \"rm -rf build\"",
        "zsh -lc 'cd x && rm -r build'",
        "eval 'rm -r build'",
        "bash -c \"bash -c 'rm -r build'\"",
        "sudo -u root rm -r build",
        "env bash -c 'rm -r build'",
        "ls | xargs -I{} sh -c 'rm -r {}'",
        "sudo -n rm -rf build",
        "env sudo rm -r build",
        "ls | xargs -I {} rm -r {}",
        "ls | xargs -n 1 rm -r",
        "( rm -rf build )",
        "cd repo  # go there\nrm -rf build",
        "rm \\\n  -rf build",
        "# it's a note\nrm -r build",
    ]
    allow = [
        "env LC_ALL=C git rm -r --cached build",
        "nohup git rm -r build",
        "ls | xargs git rm -r",
        "find . -name build -exec git rm -r {} +",
        "timeout 60 gsutil rm -r gs://x/y",
        "timeout 60 gcloud storage rm -r gs://x/y",
        "ls | xargs -I{} gsutil rm -r {}",
        "cat > notes.md <<'MY-EOF'\nrm -rf build\nMY-EOF",
        "# rm -r build",
        "echo hi # rm -r build",
        "rm build.log",
        "rm -f a.tar b.bar",
        "rm -f report.txt",
        "rm -- -r",
        "rm --force old.log",
        "trash build",
        "git rm -r build",
        "git rm -rf build",
        "gsutil rm -r gs://x/y",
        "docker rm -f box",
        "git commit -m 'rm -r build'",
        "git commit -m 'docs: why\n\nrm -r build\n'",
        "echo 'rm -rf build'",
        "for i in 1; do echo rm -r build; done",
        "grep -rn 'rm -r' tools",
        "cat > notes.md <<'EOF'\nrm -rf build\nEOF",
        "T=$(mktemp -d); trap 'rm -rf \"$T\"' EXIT; ls",
        "bash -c 'ls -r'",
        "bash script.sh rm -r",
    ]
    for c in deny:
        reason = bash_problem(c) or ""
        # An unattended session stops on a refusal unless the reason says to go on: 4 of 4 did on the first wording.
        assert "멈추지 않아도" in reason and "trash" in reason and "그대로 보고" in reason, f"should deny: {c}"
    for c in allow:
        assert bash_problem(c) is None, f"should allow: {c} -> {bash_problem(c)}"

    deny = [
        "git push --no-verify",
        "git push origin main --no-verify",
        "git push --no-verify origin main",
        "git -C ~/.dotfiles push --no-verify",
        "git -c x=y push --no-verify",
        "cd repo && git push --no-verify",
        "bash -c 'git push --no-verify'",
        "env LC_ALL=C git push --no-verify",
        "timeout 30 git push --no-verify",
        "git --git-dir .git push --no-verify",
        "git --work-tree . push --no-verify",
        "git push \\\n  --no-verify",
    ]
    allow = [
        "git push",
        "git push origin main",
        "git push --dry-run origin HEAD:refs/heads/x",
        "git commit -m 'git push --no-verify'",
        "echo git push --no-verify",
        "git -C repo log --grep=push --no-verify",
    ]
    for c in deny:
        assert "no-verify" in (bash_problem(c) or ""), f"should deny: {c}"
    for c in allow:
        assert bash_problem(c) is None, f"should allow: {c} -> {bash_problem(c)}"
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
        sys.exit()
    payload = json.load(sys.stdin)
    reason = bash_problem((payload.get("tool_input") or {}).get("command") or "") if payload.get("tool_name") == "Bash" else None
    if reason:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }}, ensure_ascii=False))
