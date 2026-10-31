#!/usr/bin/env python3
"""PreToolUse guard: edit files with the Edit tool, not `sed -i` / `perl -i` in Bash.

Edit only changes text it has read and that matches exactly once, so it cannot hit the
wrong line or overwrite another session's change. The memory rule alone ("sed/awk 금지")
was broken 45 times in 30 days (2026-10-17), because bypass mode's own instructions allow
sed edits. Reading with `sed -n` stays allowed.

Also holds the other Bash ban that must work in Claude and Codex alike: agents open panes with `pane-spawn`, never
`herdr agent start` or `herdr pane split`, so every new job gets its own named tab and a lead mark (요유 2026-10-31). It lives
here because Codex runs a new hook only after 요유 trusts it, and this command is already trusted. Jobs that run outside an
agent's Bash tool (the dispatch job) never reach this hook.

    no-sed-inplace.py --selftest
"""
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
SPAWN = re.compile(r"(?:^|[;&|(`]|\$\()\s*(?:[A-Za-z_]\w*=\S*\s+)*(?:\S*/)?herdr(?:\s+-\S+(?:\s+[^\s-]\S*)?)*"
                   r"\s+(?:agent\s+start|pane\s+split)\b(?![^\n;|&]*--help)", re.M)
QUOTED = re.compile(r"""'[^']*'|"(?:[^"\\]|\\.)*\"""")
CAPTURE = re.compile(r"\$\([^)]*\)|`[^`]*`")


def code_only(cmd: str) -> str:
    """Heredocs and quoted strings are text, also across lines (a commit message); only a capture in double quotes runs."""
    return QUOTED.sub(lambda m: " ; ".join(CAPTURE.findall(m.group())) if m.group()[0] == '"' else "''",
                      _guard.HEREDOC.sub("<<heredoc", cmd))


def bash_problem(cmd: str) -> str | None:
    if SPAWN.search(code_only(cmd)):
        return ('pane-spawn: 칸을 직접 띄우지 말 것. `pane-spawn "<일 이름>" "<지시>"`로 띄울 것: 일 이름을 붙인 새 탭에서 시작하고, '
                '호출한 칸을 👑로 표시하고, 세션을 기다려 지시까지 전달함. 같은 일의 칸(설계·구현·리뷰)만 `--same-tab`')
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
    ]
    allow = [
        "pane-spawn '설계' '지시'",
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
