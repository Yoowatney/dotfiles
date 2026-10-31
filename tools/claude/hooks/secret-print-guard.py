#!/usr/bin/env python3
"""PreToolUse guard: stop a tool call that would PRINT a secret into the transcript.

Using a secret stays allowed: read it into a variable (`T=$(...)`), `source` an
.env file, pass `$T` to curl. What is blocked is output that lands in the chat:
`cat`/`grep -n`/`head`/`python print` on a secret file, `echo $..._TOKEN`,
`printenv`, `curl -v` with credentials, token-printing commands outside `$()`,
and the Read/Grep tools on a secret file.

    secret-print-guard.py --selftest
"""
from __future__ import annotations  # a hook whose python3 is macOS's 3.9 would crash and let everything through

import json
import re
import shlex
import sys

HOME = r"(?:~|\$HOME|\$\{HOME\}|/Users/[^/\s'\"]+)"
SECRET_PATH = re.compile(
    HOME + r"/(?:\.codex/config\.toml|\.claude\.json"
    r"|\.config/[^\s'\"]*?(?:\.env|token|secret|key|cred)[^\s'\"/]*)"
    # any repo env file (.env, .env.staging, ...) except examples; values can span lines (PEM keys)
    r"|(?:^|(?<=[\s'\"=/:(]))\.env(?:\.(?!example|sample|template)[\w.-]+)?(?=$|[\s'\"|;&)<>])",
    re.I,
)
SECRET_VAR = re.compile(r"\$\{?[A-Za-z_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|PRIVATE)[A-Za-z_]*\}?", re.I)
PRINTS_SECRET = re.compile(
    r"gcloud\s+(?:auth\s+(?:application-default\s+)?print-(?:access|identity)-token"
    r"|secrets\s+versions\s+access)"
    r"|security\s+find-(?:generic|internet)-password\b[^|;&]*\s-[wg]\b"
    r"|\bop\s+read\b"
)
# ponytail: a list of commands that dump file contents; an unlisted dumper slips through,
# add it here when one does. Deny-by-default flagged every `git commit -m "...path..."`.
READERS = {
    "cat", "bat", "less", "more", "head", "tail", "sed", "awk", "gawk", "jq", "yq", "cut", "tr",
    "sort", "uniq", "nl", "column", "strings", "xxd", "od", "hexdump", "base64", "tee", "cp",
    "diff", "python", "python3", "node", "ruby", "perl", "grep", "rg", "egrep",
}
GREPS = {"grep", "rg", "egrep"}
QUIET_GREP = {"--files-with-matches", "--files-without-match", "--count", "--quiet"}
SPLIT = re.compile(r"\|\||&&|[;|\n]")
HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?[^\n]*\n.*?\n\s*\1\s*(?=\n|$)", re.S)


def strip_captures(cmd: str) -> str:
    """Drop `$( ... )` and backtick bodies: their output goes into a variable, not the chat."""
    out, depth, i = [], 0, 0
    while i < len(cmd):
        if cmd.startswith("$(", i):
            depth += 1
            i += 2
            continue
        if depth and cmd[i] == "(":
            depth += 1
        elif depth and cmd[i] == ")":
            depth -= 1
            i += 1
            continue
        if not depth:
            out.append(cmd[i])
        i += 1
    return re.sub(r"`[^`]*`", "", "".join(out))


def segments(cmd: str) -> list[list[str]]:
    """Simple commands as word lists, split on ; & | and newlines that sit outside quotes."""
    try:
        lex = shlex.shlex(cmd, posix=True, punctuation_chars=";&|\n")
        lex.whitespace = " \t\r"
        lex.whitespace_split = True
        toks = list(lex)
    except ValueError:  # unbalanced quotes: fall back to a plain split
        return [s.split() for s in SPLIT.split(cmd)]
    out, cur = [], []
    for t in toks:
        if t and set(t) <= set(";&|\n"):
            out.append(cur)
            cur = []
        else:
            cur.append(t)
    out.append(cur)
    return out


def words(w: list[str]) -> list[str]:
    while w and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w[0]):  # VAR=x cmd
        w = w[1:]
    while w and w[0] in {"sudo", "command", "exec", "time", "export"}:
        w = w[1:]
    return w


def bash_problem(cmd: str) -> str | None:
    visible = strip_captures(HEREDOC.sub("<<heredoc", cmd))  # heredoc bodies are text, not commands
    for seg_words in segments(visible):
        w = words(seg_words)
        if not w:
            continue
        segment = " ".join(w)
        if PRINTS_SECRET.match(segment):
            return "토큰이나 비밀값을 출력하는 명령은 `VAR=$(...)`로 변수에 담아서만 쓸 것"
        verb = w[0].rsplit("/", 1)[-1]
        if verb == "printenv" or (verb == "env" and all(a.startswith("-") for a in w[1:])):
            return "printenv/env는 환경변수의 비밀값을 그대로 출력함"
        if verb in {"echo", "printf"} and SECRET_VAR.search(segment):
            return "비밀값 변수를 echo/printf로 출력하지 말 것 (존재 확인은 `[ -n \"$VAR\" ] && echo set`)"
        if verb == "curl" and re.search(r"\s(?:-v|--verbose|--trace(?:-ascii)?)\b", segment) and (
            SECRET_VAR.search(cmd) or re.search(r"authorization", cmd, re.I)
        ):
            return "curl -v/--trace는 Authorization 헤더를 그대로 출력함"
        if verb == "git" and len(w) > 1 and w[1] in {"show", "cat-file"} and SECRET_PATH.search(segment):
            return "`git show`가 비밀 파일 내용을 출력함. 있는지만 볼 때는 `git ls-tree`를 쓸 것"
        readers = [b for b in (x.rsplit("/", 1)[-1] for x in w) if b in READERS]
        if readers and SECRET_PATH.search(segment):
            if set(readers) <= GREPS and (
                QUIET_GREP & set(w) or any(re.match(r"^-[a-zA-Z]*[lLcq]", a) for a in w[1:])
            ):
                continue
            return (f"`{readers[0]}`가 비밀 파일 내용을 출력할 수 있음. 값은 `VAR=$(...)`로 변수에 담아 쓰고, "
                    "있는지만 볼 때는 `grep -l`/`grep -c`/`ls`를 쓸 것")
    return None


def tool_problem(payload: dict) -> str | None:
    tool, inp = payload.get("tool_name"), payload.get("tool_input") or {}
    if tool == "Bash":
        return bash_problem(inp.get("command") or "")
    if tool == "Read" and SECRET_PATH.search(inp.get("file_path") or ""):
        return "비밀 파일을 Read로 열면 값이 전부 대화에 남음"
    if tool == "Grep" and SECRET_PATH.search(inp.get("path") or "") and inp.get("output_mode") not in {
        None, "files_with_matches", "count"
    }:
        return "비밀 파일에 Grep content 모드를 쓰면 값이 출력됨"
    return None


def selftest():
    deny = [
        "cat ~/.codex/config.toml",
        "grep -n -i slack ~/.codex/config.toml | head",
        "head -3 /Users/yoyoo/.config/gemini/.env",
        "python3 -c 'print(open(\"/Users/yoyoo/.claude.json\").read())'",
        "gcloud auth print-access-token",
        "gcloud secrets versions access latest --secret=slack-token",
        "echo $SLACK_BOT_TOKEN",
        "printf '%s' \"${GEMINI_API_KEY}\"",
        "printenv",
        "env | grep TOKEN",
        "T=$(cat ~/.config/x/.env); curl -v -H \"Authorization: Bearer $T\" https://x",
        "cat ~/.config/cloudflare/.env > /tmp/copy",
        "uv run --with x python3 dump.py ~/.codex/config.toml",
        "ls ~/.codex/hooks/ 2>/dev/null; grep -n -A8 -i \"hooks\" ~/.codex/config.toml 2>/dev/null | head -30",
        "cd /tmp && gcloud auth print-access-token",
        "sudo cat ~/.config/x/.env",
        # 2026-10-21: a multi-line PEM value spilled out of a repo env file while listing key names
        "cut -d= -f1 /Users/yoyoo/work/munice/nightly_api/.env.staging | grep -vE '^#'",
        "grep -h '^GOOGLE_PLAY_PACKAGE_NAME' .env.staging .env.production",
        "cat nightly_api/.env",
        "git show origin/main:.env.production | cut -d= -f1",
    ]
    allow = [
        "T=$(python3 -I -c 'import re,os;print(open(os.path.expanduser(\"~/.codex/config.toml\")).read())')",
        "export SLACK_BOT_TOKEN=$(jq -r .x ~/.claude.json) && python3 post.py",
        "set -a; source ~/.config/gemini/.env; set +a; curl -s -H \"x-goog-api-key: $GEMINI_API_KEY\" https://x",
        "ACCESS_TOKEN=$(gcloud auth print-access-token); curl -s -H \"Authorization: Bearer ${ACCESS_TOKEN}\" https://x",
        "grep -l slack ~/.codex/config.toml",
        "grep -c SLACK ~/.claude.json",
        "ls -la ~/.config/slack/",
        "stat ~/.config/gemini/.env",
        "cat ~/.config/mise/config.toml",
        "env FOO=1 make test",
        "[ -n \"$SLACK_BOT_TOKEN\" ] && echo set",
        "curl -v https://example.com",
        "echo done",
        "git commit -m \"stop reading ~/.claude.json with cat\"",
        "cat >> notes.md <<'EOF'\n토큰은 ~/.codex/config.toml에 있다\ncat ~/.config/x/.env 금지\nEOF\necho ok",
        "board.sh create x <<EOF\nsee ~/.claude.json\nEOF",
        "cd /tmp && codex exec \"Step 1: run cat ~/.config/x/.env (blocked is expected; do not retry). "
        "Step 2: gcloud auth print-access-token | head\" > out.txt 2>&1; echo $?",
        "git commit -m \"docs: never run cat ~/.claude.json; use grep -c\"",
        "U=$(grep '^ADMIN_USER=' /x/nightly_api/.env.production | cut -d= -f2-)",
        "set -a; source .env.staging; set +a; npm run start:dev",
        "ln -sf /x/nightly_api/.env.production /y/.env.production",
        "cat .env.example",
        "grep -c ADMIN_USER .env.staging",
        "ls -a nightly_api | grep -E '^\\.env'",
        "cat .envrc",
    ]
    for c in deny:
        assert bash_problem(c), f"should deny: {c}"
    for c in allow:
        assert bash_problem(c) is None, f"should allow: {c} -> {bash_problem(c)}"
    assert tool_problem({"tool_name": "Read", "tool_input": {"file_path": "/Users/yoyoo/.codex/config.toml"}})
    assert not tool_problem({"tool_name": "Read", "tool_input": {"file_path": "/Users/yoyoo/.claude/settings.json"}})
    assert tool_problem({"tool_name": "Grep", "tool_input": {"path": "/Users/yoyoo/.claude.json", "output_mode": "content"}})
    assert not tool_problem({"tool_name": "Grep", "tool_input": {"path": "/Users/yoyoo/.claude.json"}})
    assert tool_problem({"tool_name": "Read", "tool_input": {"file_path": "/Users/yoyoo/work/x/.env.staging"}})
    assert not tool_problem({"tool_name": "Read", "tool_input": {"file_path": "/Users/yoyoo/work/x/.env.example"}})
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
        sys.exit()
    reason = tool_problem(json.load(sys.stdin))
    if reason:  # JSON deny: Claude Code and Codex both read it (Codex's exit-2 handling is unverified)
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"secret-print-guard: {reason}",
        }}, ensure_ascii=False))
