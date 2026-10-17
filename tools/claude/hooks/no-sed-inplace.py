#!/usr/bin/env python3
"""PreToolUse guard: edit files with the Edit tool, not `sed -i` / `perl -i` in Bash.

Edit only changes text it has read and that matches exactly once, so it cannot hit the
wrong line or overwrite another session's change. The memory rule alone ("sed/awk 금지")
was broken 45 times in 30 days (2026-10-17), because bypass mode's own instructions allow
sed edits. Reading with `sed -n` stays allowed.

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


def bash_problem(cmd: str) -> str | None:
    visible = _guard.strip_captures(_guard.HEREDOC.sub("<<heredoc", cmd))  # heredoc bodies are text
    for seg in _guard.segments(visible):
        w = _guard.words(seg)
        for k, tok in enumerate(w):
            flag = INPLACE.get(tok.rsplit("/", 1)[-1])
            if not flag or (k and w[k - 1] not in LAUNCHERS):
                continue
            if any(flag.match(a) for a in w[k + 1:]):
                return ("`sed -i`/`perl -i`로 파일을 고치지 말 것. Read로 읽은 뒤 Edit 도구로 고칠 것"
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
            "permissionDecisionReason": f"no-sed-inplace: {reason}",
        }}, ensure_ascii=False))
