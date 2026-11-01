#!/usr/bin/env python3
"""PreToolUse guard: stop a tool call that would PRINT a secret into the transcript.

Using a secret stays allowed: read it into a variable (`T=$(...)`), `source` an
.env file, pass `$T` to curl. What is blocked is output that lands in the chat:
`cat`/`grep -n`/`head`/`python print` on a secret file, `echo $..._TOKEN`,
`printenv`, `curl -v` with credentials, commands that answer with a stored
credential outside `$()`, BigQuery transfer configs read with their `params`,
and the Read/Grep tools on a secret file.

    secret-print-guard.py --selftest
"""
from __future__ import annotations  # a hook whose python3 is macOS's 3.9 would crash and let everything through

import json
import re
import shlex
import sys
from urllib.parse import unquote

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
    r"security\s+find-(?:generic|internet)-password\b[^|;&]*\s-[wg]\b"
    r"|\bop\s+read\b"
)
# Reads whose answer is a stored credential, as words in order: flags, `beta`, `timeout` or `do` around them
# change nothing, and a quoted mention (a commit message, a prompt) stays one word and never matches.
# Left out on purpose: `run`/`functions describe` (env holds only secret refs here), `apps:sdkconfig` (a public
# id), `get-credentials` (writes a file), and create/reset commands (not reads).
CREDENTIAL_READS = [c.split() for c in (
    "gcloud auth describe",  # credential JSON, refresh token included
    "gcloud config config-helper",  # carries an access token
    "gcloud secrets versions access",
    "gcloud services api-keys get-key-string",
    "gcloud redis instances get-auth-string",
    "gcloud domains authorization-code",
    "gcloud iap oauth-clients",  # the client secret is an output field
    "gcloud container clusters describe",  # masterAuth.clientKey
    "gcloud runtime-config variables get-value",  # the store behind functions:config
    "gcloud runtime-config variables list --values",
    "functions:secrets:access",  # firebase's words alone, so `npx firebase-tools` counts too
    "apphosting:secrets:access",
    "functions:config:get",
    "login:ci",
)]
TOKEN_VERB = re.compile(r"(?:print|generate)-(?:[a-z]+-)*token$")  # auth print-refresh-token, sql generate-login-token
CREDENTIAL_URL = re.compile(
    r"secretmanager\.googleapis\.com\S*:access|apikeys\.googleapis\.com\S*/keyString"
    r"|redis\.googleapis\.com\S*/authString"
)
# TransferConfig and TransferRun both answer with `params`, where an S3 transfer keeps its secret key.
TRANSFER_FLAG = re.compile(r"--?transfer[_-](?:config|run)\b")
TRANSFER_HOST = "bigquerydatatransfer.googleapis.com"
FIELDS = re.compile(r"(?:^|[?&'\"\s])fields=([^&'\"\n]*)", re.M)
WIDE_FIELDS = re.compile(r"^\s*$|params|[*$]|transfer(?:Configs|Runs)(?![(/\w])")  # a bare collection name selects all of it
CLIENTS = {"curl", "wget", "http", "https", "xh"}  # print the answer unless told otherwise
FETCHERS = CLIENTS | {"python", "python3", "node", "ruby", "perl", "deno", "bun"}
CAPTURE = re.compile(r"(?<!\\)\$\(((?:[^()]|\([^()]*\))*)\)|(?<!\\)`([^`]*)(?<!\\)`")
SINGLE_QUOTED = re.compile(r"'[^']*'")  # nothing runs in there: commit messages put commands in backticks
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
HEREDOC = re.compile(r"<<-?\s*['\"]?([\w.-]+)['\"]?[^\n]*\n.*?\n\s*\1\s*(?=\n|$)", re.S)


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
    # A continued line is one command, and shlex's comment skip eats the newline that ends a comment.
    cmd = cmd.replace("\\\n", " ").replace("\n", "\n\n")
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


def names(w: list[str]) -> list[str]:
    return [x.strip("()").rsplit("/", 1)[-1] for x in w]


def reads_credential(n: list[str]) -> bool:
    def in_order(seq) -> bool:
        it = iter(n)
        return all(s in it for s in seq)
    if {"help", "--help", "-h"} & set(n):
        return False
    return any(in_order(r) for r in CREDENTIAL_READS) or ("gcloud" in n and any(TOKEN_VERB.match(x) for x in n))


def rest_problem(cmd: str, shown: set[str], ran: set[str]) -> str | None:
    """The API calls behind the same reads. Takes the raw command: the URL can sit in a variable or a Python heredoc."""
    # ponytail: a script file or client library doing the same call is not seen here.
    if CLIENTS & shown and CREDENTIAL_URL.search(cmd):  # a capture or a script may only use the value
        return "이 REST 호출은 비밀값을 그대로 돌려줌. 응답은 `VAR=$(...)`로 변수에 담아서만 쓸 것"
    if FETCHERS & ran and TRANSFER_HOST in cmd and "transferConfigs" in cmd:
        fields = [unquote(f) for f in FIELDS.findall(cmd)]
        # One `fields=` per host mention, so a second call can't lean on the first's. A host in a variable counts once.
        if len(fields) < cmd.count(TRANSFER_HOST) or any(WIDE_FIELDS.search(f) for f in fields):
            return ("전송 구성 응답의 `params`에는 저장된 자격 증명이 들어 있음. "
                    "`fields=transferConfigs(name,displayName,schedule)`처럼 `params`를 뺀 필드만 고를 것")
    return None


def bash_problem(cmd: str) -> str | None:
    text = HEREDOC.sub("<<heredoc", cmd)  # heredoc bodies are text, not commands
    visible = strip_captures(text)
    seen = segments(visible)
    # A capture keeps one value out of the chat, but a transfer config is a whole answer read in order to print it.
    captured = [s for pair in CAPTURE.findall(SINGLE_QUOTED.sub("''", text)) for s in segments("".join(pair))]
    shown = {x for seg in seen for x in names(seg)}
    ran = shown | {x for seg in captured for x in names(seg)}
    for seg in seen + captured:
        n = names(seg)
        if "bq" in n and {"ls", "show"} & set(n) and any(TRANSFER_FLAG.match(x) for x in seg):
            return ("`bq ls/show --transfer_config`와 `--transfer_run`은 저장된 자격 증명을 `params`째 돌려줌. "
                    "Data Transfer REST API에 `fields=`로 `params`를 빼고 읽을 것")
    problem = rest_problem(cmd, shown, ran)
    if problem:
        return problem
    for seg_words in seen:
        w = words(seg_words)
        if not w:
            continue
        segment = " ".join(w)
        if PRINTS_SECRET.match(segment) or reads_credential(names(w)):
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
    TC = "https://bigquerydatatransfer.googleapis.com/v1/projects/p/locations/l/transferConfigs"
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
        "cd repo  # go there\ncat ~/.config/gemini/.env",
        "cat \\\n  ~/.config/gemini/.env",
        "uv run --with x python3 dump.py ~/.codex/config.toml",
        "ls ~/.codex/hooks/ 2>/dev/null; grep -n -A8 -i \"hooks\" ~/.codex/config.toml 2>/dev/null | head -30",
        "cd /tmp && gcloud auth print-access-token",
        "sudo cat ~/.config/x/.env",
        # 2026-10-21: a multi-line PEM value spilled out of a repo env file while listing key names
        "cut -d= -f1 /Users/yoyoo/work/munice/nightly_api/.env.staging | grep -vE '^#'",
        "grep -h '^GOOGLE_PLAY_PACKAGE_NAME' .env.staging .env.production",
        "cat nightly_api/.env",
        "git show origin/main:.env.production | cut -d= -f1",
        # 2026-11-01: a transfer config came back with its stored S3 secret key
        "bq ls --transfer_config --transfer_location=us",
        "bq show --transfer_config projects/p/locations/l/transferConfigs/c",
        "bq --project_id=p --format=prettyjson ls --transfer_config --transfer_location=us",
        "bq ls --format=json -transfer_config | jq .",
        "bq ls --transfer_run --run_attempt=LATEST projects/p/locations/l/transferConfigs/c",
        "bq show --transfer_run projects/p/locations/l/transferConfigs/c/runs/r",
        "timeout 60 bq ls --transfer_config",
        "for l in us eu; do bq ls --transfer_config --transfer_location=$l; done",
        "echo c | xargs bq show --transfer_config",
        "(/opt/sdk/bin/bq ls --transfer_config)",
        "PATH=/var/empty bq ls --transfer_config",
        "C=$(bq ls --transfer_config --transfer_location=us); echo \"$C\" | head",
        "echo \"cfg: `bq ls --transfer_config`\"",
        f"curl -s -H \"Authorization: Bearer $T\" \"{TC}\"",
        f"curl -s -H \"Authorization: Bearer $(gcloud auth print-access-token)\" \"{TC}\" | jq .",
        f"curl \"{TC}/c\"",
        f"curl \"{TC}/c/runs\"",
        f"curl \"{TC}?fields=transferConfigs\"",
        f"curl \"{TC}?fields=*\"",
        f"curl \"{TC}?fields=transferConfigs(*)\"",
        f"curl \"{TC}?fields=transferConfigs(name,params)\"",
        f"curl \"{TC}?fields=transferConfigs(params/secret_access_key)\"",
        f"curl \"{TC}?fields=transferConfigs%28name%2Cparams%29\"",
        f"curl \"{TC}?fields=transferConfigs(name)\"; curl \"{TC}/c\"",
        f"wget -qO- '{TC}'",
        "B=https://bigquerydatatransfer.googleapis.com/v1/projects/p/locations/l; curl -s \"$B/transferConfigs\"",
        f"R=$(curl -s \"{TC}\"); echo \"$R\" | head",
        f"python3 - <<'PY'\nimport urllib.request as u\nprint(u.urlopen('{TC}').read())\nPY",
        # the same kind: a read whose answer is a stored credential
        "gcloud auth print-refresh-token",
        "gcloud --project p secrets versions access latest --secret=x",
        "gcloud beta secrets versions access latest --secret=x",
        "timeout 30 gcloud auth print-access-token",
        "gcloud container aws clusters print-access-token c",
        "gcloud auth describe a@example.com",
        "gcloud config config-helper --format=json",
        "gcloud services api-keys get-key-string k",
        "gcloud alpha services api-keys get-key-string k",
        "gcloud redis instances get-auth-string r --region=l",
        "gcloud sql generate-login-token",
        "gcloud domains registrations authorization-code get example.com",
        "gcloud iap oauth-clients list projects/p/brands/b",
        "gcloud beta runtime-config configs variables get-value v --config-name=c",
        "gcloud beta runtime-config configs variables list --config-name=c --values",
        "gcloud container clusters describe c",
        "firebase functions:secrets:access X",
        "npx -y firebase-tools --project p functions:secrets:access X",
        "firebase apphosting:secrets:access X",
        "firebase functions:config:get",
        "firebase login:ci",
        "curl -s -H \"Authorization: Bearer $T\" "
        "\"https://secretmanager.googleapis.com/v1/projects/p/secrets/s/versions/latest:access\"",
        "curl -s \"https://apikeys.googleapis.com/v2/projects/p/locations/global/keys/k/keyString\"",
        "curl -s \"https://redis.googleapis.com/v1/projects/p/locations/l/instances/i/authString\"",
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
        f"curl -s -H \"Authorization: Bearer $T\" \"{TC}?fields=transferConfigs(name,displayName,schedule),nextPageToken\"",
        f"curl -s \"{TC}/c?fields=name,displayName,state\"",
        f"curl -s \"{TC}/c/runs?fields=transferRuns(name,state,errorStatus)\"",
        f"curl -s \"{TC}?fields=transferConfigs%28name%2Cschedule%29\"",
        f"curl -sG \"{TC}\" --data-urlencode \"fields=transferConfigs(name,state)\"",
        f"R=$(curl -s \"{TC}?fields=transferConfigs(name)\"); echo \"$R\" | jq .",
        "curl -s \"https://bigquerydatatransfer.googleapis.com/v1/projects/p/locations/l/dataSources\"",
        "git commit -m \"block bq ls --transfer_config\"",
        "git commit -q -m 'fix(hooks): stop reads\n\n`bq ls --transfer_config` returned keys, `curl` too\n' -- x.py",
        "git commit -m \"fix \\`bq ls --transfer_config\\` and \\$(bq show --transfer_run)\"",
        f"gh issue comment 1 --body 'use `curl` on {TC}'",
        f"git commit -m \"curl {TC} is blocked\"",
        f"cat >> notes.md <<'EOF'\nbq ls --transfer_config 금지\ncurl {TC}\nEOF",
        "board.sh create x 'bq show --transfer_config 금지'",
        "rg 'bq ls --transfer_config' tools",
        "bq ls --transfer_log projects/p/locations/l/transferConfigs/c/runs/r",
        "bq ls --transfer_location=us --max_results=5 d",
        "bq mk --transfer_config --target_dataset=d --display_name=x --data_source=y",
        "bq ls --format=json d",
        "bq show d.t",
        "K=$(gcloud services api-keys get-key-string k); curl -s -H \"x-goog-api-key: $K\" https://x",
        "P=$(firebase functions:secrets:access X) && ./deploy.sh",
        "T=$(gcloud auth print-refresh-token)",
        "gcloud services api-keys list",
        "gcloud auth list",
        "gcloud auth configure-docker",
        "gcloud config get-value project",
        "gcloud run services describe api --region=l --format='value(status.url)'",
        "gcloud secrets versions list s",
        "gcloud secrets describe s",
        "firebase functions:secrets:get X",
        "firebase apps:sdkconfig WEB",
        "gcloud auth describe --help",
        "gcloud help services api-keys get-key-string",
        "firebase help login:ci",
        "V=$(curl -s \"https://secretmanager.googleapis.com/v1/projects/p/secrets/s/versions/latest:access\" | jq -r .x)",
        "python3 - <<'PY'\nu = 'https://secretmanager.googleapis.com/v1/projects/p/secrets/s/versions/latest:access'\nPY",
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
