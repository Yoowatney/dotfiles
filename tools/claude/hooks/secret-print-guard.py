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
    # Claude's deny list (settings.json) keeps these from Claude; Codex reads through the shell only, so this guard covers both.
    r"|\.(?:ssh|aws|gnupg)/[^\s'\"]+(?<!\.pub)(?=$|[\s'\"|;&)<>])|\.config/gh/hosts\.yml"
    r"|\.config/[^\s'\"]*?(?:\.env|token|secret|key|cred)[^\s'\"/]*)"
    # any repo env file (.env, .env.staging, ...) except examples; values can span lines (PEM keys)
    r"|(?:^|(?<=[\s'\"=/:(]))(?:\.env(?:\.(?!example|sample|template)[\w.-]+)?"
    # the same values in a tracked file (`envs/env.mjs`), and key files by their usual names
    # ponytail: by name only; `rg -n TOKEN packages/` or a key file under another name still prints.
    r"|env\.(?:mjs|cjs|js|ts|json|ya?ml)"
    r"|\.runtimeconfig\.json"
    r"|[\w.-]*(?:credential|service[-_]?account|adminsdk)[\w.-]*\.json"
    r"|[\w.-]+(?<![-._]pub)\.(?:pem|p12|p8|pfx))(?=$|[\s'\"|;&)<>?])",
    re.I,
)
# A search of the folder that holds a secret file prints that file too (`rg -n slack ~/.codex`, `~/.codex/*.toml`).
SECRET_DIR = re.compile(HOME + r"/\.(?:codex|config|ssh|aws|gnupg)/?(?:\*[^\s'\"/]*)?(?=$|[\s'\"|;&)<>])")
# ponytail: after `cd ~/.codex` only `config.toml` by name is seen; `rg x .` or a bare `rg x` there is not.
CD_CODEX = re.compile(r"\bcd\s+" + HOME + r"/\.codex/?(?=$|[\s;&|)])")
SECRET_VAR =re.compile(r"\$\{?[A-Za-z_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|PRIVATE)[A-Za-z_]*\}?", re.I)
PRINTS_SECRET = re.compile(
    r"security\s+find-(?:generic|internet)-password\b[^|;&]*\s-[wg]\b"
    r"|\bop\s+read\b"
)
# Reads whose answer is a stored credential, as words in order: flags, `beta`, `timeout` or `do` around them
# change nothing, and a quoted mention (a commit message, a prompt) stays one word and never matches.
# Left out on purpose: `run`/`functions describe` (350 uses in three weeks, and env is meant to hold secret refs),
# `apps:sdkconfig` (a public id), `get-credentials` (writes a file), and create/reset commands (not reads).
CREDENTIAL_READS = [c.split() for c in (
    "gh auth token",  # the GitHub login itself
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
    "gcloud parametermanager render",  # renderedPayload fills in Secret Manager values
    "gcloud iam oauth-clients credentials",  # clientSecret
    "gcloud compute interconnects macsec get-config",  # cak
    "gcloud vmware credentials describe",  # password
    "firebase functions:secrets:access",
    "firebase apphosting:secrets:access",
    "firebase functions:config:get",
    "firebase login:ci",
)]
TOKEN_VERB = re.compile(r"(?:print|generate|fetch)-(?:[a-z]+-)*token$")  # auth print-refresh-token, sql generate-login-token
HELP = {"help", "--help", "-h"}
SCHEDULER = "gcloud scheduler jobs".split()
NARROW_FORMAT = re.compile(r"--format=(?:value|csv|table|get)\((?!.*headers)")
CREDENTIAL_URL = re.compile(
    r"secretmanager\.googleapis\.com\S*:access|apikeys\.googleapis\.com\S*/keyString"
    r"|redis\.googleapis\.com\S*/authString"
)
# TransferConfig and TransferRun both answer with `params`, where an S3 transfer keeps its secret key.
TRANSFER_FLAG = re.compile(r"--?transfer[_-](config|run)\b")
TRANSFER_HOST = "bigquerydatatransfer.googleapis.com"
FIELDS = re.compile(r"(?:^|[?&'\"\s])fields=([^&'\"\n]*)", re.M)
WIDE_FIELDS = re.compile(r"^\s*$|params|[*$]|\b(?:transferConfigs|transferRuns|runs)(?![(/\w])")  # a bare collection selects all of it
CLIENTS = {"curl", "wget", "http", "https", "xh"}  # print the answer unless told otherwise
FETCHERS = CLIENTS | {"python", "node", "ruby", "perl", "deno", "bun"}  # compared without a version, so python3.12 counts
CAPTURE = re.compile(r"(?<!\\)\$\(((?:[^()]|\([^()]*\))*)\)|(?<!\\)`([^`]*)(?<!\\)`")
QUOTED = re.compile(r"""'[^']*'|"(?:[^"\\]|\\.)*\"""")  # double quotes are matched so an apostrophe in them opens nothing
SHELLS = {"bash", "sh", "zsh"}
SHELL_C = re.compile(r"-[a-zA-Z]*c[a-zA-Z]*")
# ponytail: a list of commands that dump file contents; an unlisted dumper slips through,
# add it here when one does. Deny-by-default flagged every `git commit -m "...path..."`.
READERS = {
    "cat", "bat", "less", "more", "head", "tail", "sed", "awk", "gawk", "jq", "yq", "cut", "tr",
    "sort", "uniq", "nl", "column", "strings", "xxd", "od", "hexdump", "base64", "tee", "cp",
    "diff", "python", "python3", "node", "ruby", "perl", "deno", "bun", "grep", "rg", "egrep",
}
GREPS = {"grep", "rg", "egrep"}
QUIET_GREP = {"--files-with-matches", "--files-without-match", "--count", "--quiet", "--files"}
GIT_QUIET = {"--stat", "--shortstat", "--numstat", "--name-only", "--name-status", "--quiet"}
GIT_PATCH = re.compile(r"^(?:-p|-u|-U\d*|-c|--cc|--patch.*|--unified.*)$")
SPLIT =re.compile(r"\|\||&&|[;|\n]")
HEREDOC = re.compile(r"<<-?\s*['\"]?([\w.-]+)['\"]?([^\n]*)\n(.*?)\n\s*\1\s*(?=\n|$)", re.S)
# ponytail: a heredoc handed to bash/sh is still read as text; check its body like a command if one leaks that way.
INTERPRETER = re.compile(r"(?:^|[\s(/])(?:python[\d.]*|node|ruby|perl|deno|bun)(?=[\s;&|)]|$)")


def is_reader(x: str) -> bool:
    return x in READERS or x.rstrip("0123456789.") in READERS  # python3.12 is python


def git_prints_file(w: list[str]) -> str | None:
    """The git subcommand when it prints a file's lines; their quiet forms and a plain `git log` only name the file."""
    i = 1
    while i < len(w) and w[i].startswith("-"):  # `git -C dir show`
        i += 2 if w[i] in {"-C", "-c", "--git-dir", "--work-tree"} else 1
    sub, rest = w[i] if i < len(w) else "", w[i + 1:]
    flags, patch = set(rest), any(GIT_PATCH.match(a) for a in rest)
    if sub == "log":
        return sub if patch or any(a.startswith("-L") for a in rest) else None
    if sub == "blame":
        return sub  # `blame -s` only shortens the columns
    if sub == "cat-file":
        return None if flags & {"-s", "-t", "-e"} else sub
    if sub == "grep":
        return None if QUIET_GREP & flags or any(re.match(r"^-[a-zA-Z]*[lLcq]", a) for a in rest) else sub
    if sub in {"show", "diff"}:
        # `git show --stat HEAD:env.mjs` prints the blob whatever the flag, and `--stat -p` prints the patch too.
        blob = sub == "show" and any(":" in a and SECRET_PATH.search(a) for a in rest)
        return None if GIT_QUIET & flags and not (patch or blob) else sub
    return None


def heredoc(m: re.Match, text: str) -> str:
    """A heredoc body is text, unless its line hands it to an interpreter: then the secret paths in it get read.

    The rest of the marker's line stays, since `<<'EOF' && cat secret` runs that command too.
    """
    line = text[:m.start()].rsplit("\n", 1)[-1] + m[2]
    paths = SECRET_PATH.findall(m[3]) if INTERPRETER.search(line) else []
    return " ".join(["<<heredoc", *paths]) + m[2]


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


def strip_comments(cmd: str) -> str:
    """Drop `# ...` up to the line's end where the shell does: only a `#` that opens a word, outside quotes.
    shlex also starts a comment at a `#` inside a word (`${#S}`, `a#b`), which hid the rest of the line (2026-11-06)."""
    out, quote, i = [], None, 0
    while i < len(cmd):
        c = cmd[i]
        if c == "\\" and quote != "'" and i + 1 < len(cmd):
            out += cmd[i:i + 2]
            i += 2
            continue
        if quote:
            quote = None if c == quote else quote
        elif c in "'\"":
            quote = c
        elif c == "#" and (not out or out[-1] in " \t\r\n;&|()<>"):
            end = cmd.find("\n", i)
            i = len(cmd) if end < 0 else end
            continue
        out.append(c)
        i += 1
    return "".join(out)


def segments(cmd: str) -> list[list[str]]:
    """Simple commands as word lists, split on ; & | and newlines that sit outside quotes."""
    cmd = strip_comments(cmd.replace("\\\n", " "))  # a continued line is one command
    try:
        lex = shlex.shlex(cmd, posix=True, punctuation_chars=";&|\n")
        lex.commenters = ""  # strip_comments did the shell's part
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
    """Program names of a word list. A word with a space in it is quoted text, not a path."""
    return [x if re.search(r"\s", x) else x.strip("<>()").rsplit("/", 1)[-1] for x in w]


def in_order(n: list[str], seq) -> bool:
    it = iter(n)
    return all(s in it for s in seq)


def reads_credential(n: list[str]) -> bool:
    n = [re.sub(r"^firebase-tools.*", "firebase", x) for x in n]  # `npx firebase-tools` is the same program
    if HELP & set(n):
        return False
    return any(in_order(n, r) for r in CREDENTIAL_READS) or ("gcloud" in n and any(TOKEN_VERB.match(x) for x in n))


def dumps_scheduler_job(w: list[str]) -> bool:
    """A job comes back with its custom HTTP headers as they are, and a shared secret can sit in one."""
    n, fmt = names(w), next((x for x in w if x.startswith("--format=")), "")
    if HELP & set(n):
        return False
    if in_order(n, SCHEDULER + ["describe"]):
        return not NARROW_FORMAT.match(fmt)
    return in_order(n, SCHEDULER + ["list"]) and re.match(r"--format=(?:json|yaml)", fmt) is not None


def reads_transfer(seg: list[str]) -> bool:
    n, flags = names(seg), {m[1] for m in map(TRANSFER_FLAG.match, seg) if m}
    return "bq" in n and bool(({"ls", "show"} & set(n) and flags) or ("mk" in n and "run" in flags))


def shell_text(w: list[str]) -> str | None:
    """What `bash -c` and `eval` are handed. A short copy of no-sed-inplace's walk, since that file imports this one."""
    n = names(w)
    if n and n[0] == "eval":
        return " ".join(w[1:])
    for k, x in enumerate(n[:-2]):
        if x in SHELLS and SHELL_C.fullmatch(w[k + 1]):
            return w[k + 2]
    return None


def reads_secret(body: str) -> bool:
    """A capture body whose output is a secret: the same reads this guard blocks when printed."""
    if CREDENTIAL_URL.search(body):
        return True
    for seg in segments(body):
        w = words(seg)
        n = names(w)
        if PRINTS_SECRET.match(" ".join(w)) or reads_credential(n) or (
            any(map(is_reader, n)) and SECRET_PATH.search(" ".join(w))
        ):
            return True
    return False


def secret_vars(runs: str) -> set[str]:
    """Names assigned from a secret read: `S=$(...)`, `export S="$(...)"`, `S=`...``."""
    # ponytail: a second hop (`T=$S`), `read S < <(...)` and `for S in $(...)` are not followed.
    return {
        v[1] for m in CAPTURE.finditer(runs)
        if (v := re.search(r"(?<![\w$])([A-Za-z_]\w*)=\"?$", runs[:m.start()])) and reads_secret(m[1] or m[2])
    }


def rest_problem(cmd: str, shown: set[str], ran: list[str]) -> str | None:
    """The API calls behind the same reads. Takes the raw command: the URL can sit in a variable or a Python heredoc."""
    # ponytail: a script file or client library doing the same call is not seen here.
    if CLIENTS & shown and CREDENTIAL_URL.search(cmd):  # a capture or a script may only use the value
        return "이 REST 호출은 비밀값을 그대로 돌려줌. 응답은 `VAR=$(...)`로 변수에 담아서만 쓸 것"
    if TRANSFER_HOST in cmd and "transferConfigs" in cmd and any(x.rstrip("0123456789.") in FETCHERS for x in ran):
        fields = [unquote(f) for f in FIELDS.findall(cmd)]
        # One `fields=` per call, so a second call can't lean on the first's, even with the URL in a variable.
        calls = max(cmd.count(TRANSFER_HOST), sum(x in CLIENTS for x in ran))
        if len(fields) < calls or any(WIDE_FIELDS.search(f) for f in fields):
            return ("전송 구성 응답의 `params`에는 저장된 자격 증명이 들어 있음. "
                    "`fields=transferConfigs(name,displayName,schedule)`처럼 `params`를 뺀 필드만 고를 것")
    return None


def bash_problem(cmd: str) -> str | None:
    text = HEREDOC.sub(lambda m: heredoc(m, cmd), cmd)
    visible = strip_captures(text)
    if CD_CODEX.search(visible):
        visible = re.sub(r"(?<![\w/.-])config\.toml\b", "~/.codex/config.toml", visible)
    seen = segments(visible)
    # A capture keeps one value out of the chat, but a transfer config is a whole answer read in order to print it.
    runs = QUOTED.sub(lambda m: "''" if m[0][0] == "'" else m[0], text)  # nothing runs in single quotes
    captured = [s for pair in CAPTURE.findall(runs) for s in segments("".join(pair))]
    # `${#S}` prints only the length; every other expansion of S prints some of the value.
    tainted = [re.compile(rf"\$(?:{v}\b|\{{(?!#){v}\b)") for v in secret_vars(runs)]
    shown = {x for seg in seen for x in names(seg)}
    ran = [x for seg in seen + captured for x in names(seg)]
    if any(reads_transfer(seg) for seg in seen + captured):
        return ("`bq`의 `--transfer_config`, `--transfer_run` 응답에는 저장된 자격 증명이 `params`째 들어 있음. "
                "Data Transfer REST API에 `fields=`로 `params`를 빼고 읽을 것")
    problem = rest_problem(cmd, shown, ran)
    if problem:
        return problem
    for seg_words in seen:
        w = words(seg_words)
        if not w:
            continue
        inner = shell_text(w)
        problem = bash_problem(inner) if inner else None
        if problem:
            return problem
        segment = " ".join(w)
        if PRINTS_SECRET.match(segment) or reads_credential(names(w)):
            return "토큰이나 비밀값을 출력하는 명령은 `VAR=$(...)`로 변수에 담아서만 쓸 것"
        if dumps_scheduler_job(w):
            return "Scheduler 잡 응답의 HTTP 헤더에 비밀값이 있을 수 있음. `--format='value(name,schedule,state)'`처럼 필드를 골라 읽을 것"
        verb = w[0].rsplit("/", 1)[-1]
        if verb == "printenv" or (verb == "env" and all(a.startswith("-") for a in w[1:])):
            return "printenv/env는 환경변수의 비밀값을 그대로 출력함"
        if verb in {"echo", "printf", "print"} and (
            SECRET_VAR.search(segment) or any(t.search(segment) for t in tainted)
        ):
            return ("비밀값 변수를 echo/printf로 출력하지 말 것. 앞 몇 글자만 찍어도 값이 새어 나감 "
                    "(존재 확인은 `[ -n \"$VAR\" ] && echo set`, 길이는 `echo ${#VAR}`)")
        if verb == "curl" and re.search(r"\s(?:-v|--verbose|--trace(?:-ascii)?)\b", segment) and (
            SECRET_VAR.search(cmd) or re.search(r"authorization", cmd, re.I)
        ):
            return "curl -v/--trace는 Authorization 헤더를 그대로 출력함"
        if verb == "git":  # `diff` and `grep` here are subcommands, not the readers listed below
            if SECRET_PATH.search(segment) and (sub := git_prints_file(w)):
                return f"`git {sub}`가 비밀 파일 내용을 출력함. 있는지만 볼 때는 `git ls-tree`나 `git log --oneline`을 쓸 것"
            continue
        if verb == "gh" and w[1:2] == ["api"] and SECRET_PATH.search(segment):
            return "`gh api`가 비밀 파일 내용을 돌려줌. 값은 `VAR=$(...)`로 변수에 담아서만 쓸 것"
        readers =[b for b in (x.rsplit("/", 1)[-1] for x in w) if is_reader(b)]
        if readers and (SECRET_PATH.search(segment) or SECRET_DIR.search(segment)):
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


def git_quiet_forms_print_nothing():
    """The git forms the guard lets through, run on a real repo: a flag that only looks quiet fails here."""
    import pathlib
    import subprocess
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        def git(*a):
            return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=d, capture_output=True, text=True).stdout
        git("init", "-q")
        for line in ("CANARY 1\n", "CANARY 2\n"):
            pathlib.Path(d, "env.mjs").write_text(line)
            git("add", ".")
            git("commit", "-qm", "m")
        for c in (
            "git log --oneline -5 -- env.mjs", "git log --stat -- env.mjs", "git ls-tree HEAD env.mjs",
            "git diff --stat HEAD~1 -- env.mjs", "git diff --name-only HEAD~1 -- env.mjs", "git diff --quiet HEAD~1 -- env.mjs",
            "git grep -l CANARY -- env.mjs", "git grep -c CANARY -- env.mjs", "git cat-file -s HEAD:env.mjs",
            "git cat-file -t HEAD:env.mjs", "git show --stat HEAD -- env.mjs", "git show --name-only HEAD -- env.mjs",
        ):
            assert bash_problem(c) is None, f"should allow: {c}"
            assert "CANARY" not in git(*c.split()[1:]), f"prints the file: {c}"


def selftest():
    git_quiet_forms_print_nothing()
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
        # found by the refutation pass
        "gcloud parametermanager parameters versions render v --parameter=p --location=global",
        "gcloud developer-connect connections git-repository-links fetch-read-token l --connection=c --region=r",
        "gcloud iam oauth-clients credentials describe c --oauth-client=o --location=global",
        "gcloud compute interconnects macsec get-config i",
        "gcloud vmware private-clouds vcenter credentials describe --private-cloud=p --location=l",
        "gcloud scheduler jobs describe j --location=l",
        "gcloud scheduler jobs describe j --location=l --format=json",
        "gcloud scheduler jobs describe j --format='value(httpTarget.headers)'",
        "gcloud scheduler jobs list --location=l --format=json",
        "bq mk --transfer_run --run_time=t projects/p/locations/l/transferConfigs/c",
        "diff <(bq show --transfer_config a) <(bq show --transfer_config b)",
        "bash -c \"bq ls --transfer_config\"",
        "timeout 30 zsh -lc 'gcloud auth print-access-token'",
        "eval \"bq ls --transfer_config\"",
        f"curl \"{TC}/c:startManualRuns?fields=runs\"",
        f"B={TC}; curl \"$B?fields=transferConfigs(name)\"; curl \"$B/c\"",
        f"python3.12 - <<'PY'\nprint('{TC}')\nPY",
        # 2026-11-03: a secret read into a plain-named variable, then its first characters echoed
        "S=$(gcloud secrets versions access latest --secret=x); echo \"${S:0:4}\"",
        "S=$(gcloud secrets versions access latest --secret=x | tr -d '\\n'); printf '%s\\n' \"${S}\"",
        "export S=\"$(gcloud secrets versions access latest --secret=x)\" && echo $S",
        "S=`gcloud secrets versions access latest --secret=x`; echo $S",
        "S=$(security find-generic-password -s x -w); echo ${S:0:4}",
        "S=$(cat ~/.config/x/.env); echo $S",
        "S=$(firebase functions:secrets:access X); echo \"len=${#S} head=${S:0:4}\"",
        "S=$(gcloud secrets versions access latest --secret=x); echo \"${S%????????}\"",
        # 2026-11-05 (api#1035): a Codex pane printed the Slack token with a Python heredoc that grepped config.toml
        "python3 - <<'PY'\nfrom pathlib import Path\nfor f in ['/Users/yoyoo/.codex/config.toml']:\n"
        " for n,l in enumerate(Path(f).read_text().splitlines(),1):\n  if 'slack' in l: print(f'{f}:{n}:{l}')\nPY",
        "python3 - <<'PY' 2>&1 | head\nprint(open('/Users/yoyoo/.codex/config.toml').read())\nPY",
        "uv run --with x python3 - <<'PY'\nimport os\nprint(open(os.path.expanduser('~/.codex/config.toml')).read())\nPY",
        "node - <<'JS'\nconsole.log(require('fs').readFileSync('/Users/yoyoo/.claude.json','utf8'))\nJS",
        "cat > /tmp/a.py <<'PY' && python3 /tmp/a.py\nprint(open('/Users/yoyoo/.codex/config.toml').read())\nPY",
        "cat > /tmp/a.txt <<'EOF' && cat ~/.codex/config.toml\nx\nEOF",
        "python3.12 -c 'print(open(\"/Users/yoyoo/.codex/config.toml\").read())'",
        # the same kind: a search of the folder that holds the file, or the file by its short name
        "rg -n slack ~/.codex",
        "rg -n slack /Users/yoyoo/.codex/",
        "rg -n -i token ~/.config",
        "grep -rn SLACK ~/.codex",
        "rg -n xoxb ~/.codex/*.toml",
        "cd ~/.codex && rg slack config.toml",
        "cd /Users/yoyoo/.codex; sed -n '140,150p' config.toml",
        # 2026-11-06: a repo keeps its values in a tracked `envs/env.mjs`, and `sed -n` printed them into a session
        "sed -n 75,90p packages/utils/src/envs/env.mjs",
        "cd /x/app; sed -n 75,90p packages/utils/src/envs/env.mjs; ls",
        "grep -n -i key packages/utils/src/envs/env.mjs | head",
        "git show origin/main:packages/utils/src/envs/env.mjs | grep -n -i key",
        "git show origin/main:env.mjs | sed -E 's/[0-9a-f]{32}/<HEX32>/g'",
        "cat apps/admin/env.ts",
        "cat env.js env.cjs",
        "jq . config/env.json",
        "python3 - <<'PY'\nprint(open('packages/utils/src/envs/env.mjs').read())\nPY",
        # the same kind: key files by their usual names
        "cat android/credential-prod.json",
        "cat gcp-credentials.json",
        "cat functions/.runtimeconfig.json",
        "jq . service-account.json",
        "cat serviceAccountKey.json",
        "cat service_account.json",
        "head x-firebase-adminsdk-abc12.json",
        "cat key.pem",
        "cat AuthKey_ABC.p8",
        "gh api repos/o/r/contents/android/credential-prod.json",
        "gh api repos/o/r/contents/.env?ref=main --jq .content | base64 -d",
        # the same kind: every git command that prints a file's lines, not only `git show`
        "git -C /x/app show origin/main:packages/utils/src/envs/env.mjs",
        "git grep -n KEY origin/main -- packages/utils/src/envs/env.mjs",
        "git grep -n TOKEN -- .env.staging",
        "git diff HEAD~1 -- packages/utils/src/envs/env.mjs",
        "git log -p -- .env.production",
        "git log --oneline -p -- packages/utils/src/envs/env.mjs",
        "git log -L 1,9:env.mjs",
        "git blame env.mjs",
        "git cat-file -p HEAD:env.mjs",
        # found by the Codex review: flags that look quiet and still print the lines
        "git show --stat HEAD:env.mjs",
        "git show --name-only HEAD:env.mjs",
        "git blame -s env.mjs",
        "git diff --stat -p -- env.mjs",
        "git cat-file blob HEAD:env.mjs",
        "git log -U3 -- env.mjs",
        "git log --patch-with-stat -- env.mjs",
        # 2026-11-06: shlex read a `#` inside a word as a comment and skipped the rest of the line; the shell does not
        ": ${#Z}; gcloud secrets versions access latest --secret=x",
        "echo a#b; cat ~/.codex/config.toml",
        "curl -s https://x/#frag && gcloud auth print-access-token",
        "S=$(: ${#Z}; gcloud secrets versions access latest --secret=x); echo \"$S\"",
        # 2026-11-07: Claude's deny list keeps these from its Read tool and its shell; Codex reads only through the shell.
        "gh auth token",
        "gh auth token --hostname github.com | pbcopy",
        "cat ~/.ssh/id_ed25519",
        "head -2 /Users/yoyoo/.ssh/id_rsa",
        "cat ~/.aws/credentials",
        "cat ~/.gnupg/private-keys-v1.d/x.key",
        "cat ~/.config/gh/hosts.yml",
        "grep -rn oauth ~/.ssh",
    ]
    allow = [
        "ls ~/.ssh",
        "cat ~/.ssh/id_ed25519.pub",
        "ssh -o BatchMode=yes host true",
        "gh auth status",
        "T=$(gh auth token); curl -s -H \"Authorization: Bearer $T\" https://x",
        "ls  # cat ~/.codex/config.toml",
        "gcloud secrets list --project=p # api#973",
        "echo 'a #b'; ls",
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
        "rg \"functions:config:get\" src",
        "git commit -m \"functions:secrets:access\"",
        "git commit -m \"dont't\" -m 'use `bq ls --transfer_config`'",
        f"git commit -m \"see /usr/bin/curl\" -m \"{TC}\"",
        "gcloud scheduler jobs list --location=l",
        "gcloud scheduler jobs describe j --format='value(schedule,state)'",
        f"curl \"{TC}/c:startManualRuns?fields=runs(name,state)\"",
        "bash -c \"ls -la\"",
        "zsh -lc 'gcloud auth list'",
        "S=$(gcloud secrets versions access latest --secret=x); echo ${#S}",
        "S=$(gcloud secrets versions access latest --secret=x); echo \"len=${#S}\"",
        "S=$(gcloud secrets versions access latest --secret=x); curl -s -H \"Authorization: Bearer $S\" https://x",
        "S=$(gcloud secrets versions list s); echo $S",
        "S=$(gcloud secrets versions access latest --secret=x); echo $SX ${SX:0:4}",
        "git commit -m 'S=$(gcloud secrets versions access latest --secret=x); echo ${S:0:4}'",
        "T=$(python3 - <<'PY'\nimport tomllib\nprint(tomllib.load(open('/Users/yoyoo/.codex/config.toml','rb'))['x'])\nPY\n)",
        "python3 - <<'PY'\nprint('see ~/.codex/hooks.json')\nPY",
        "cat > notes.md <<'EOF'\npython3 reads ~/.codex/config.toml\nEOF",
        "rg -l slack ~/.codex",
        "rg --files ~/.codex",
        "rg -n hook ~/.codex/hooks.json",
        "rg -n x ~/.codex/hooks ~/.config/mise",
        "ls -la ~/.codex ~/.config",
        "cd ~/.codex && cat hooks.json",
        "cd ~/.dotfiles && cat tools/mise/config.toml",
        "grep -c -i key packages/utils/src/envs/env.mjs",
        "rg -l KEY packages/utils/src/envs/env.mjs",
        "ls -la packages/utils/src/envs/env.mjs",
        "wc -l packages/utils/src/envs/env.mjs",
        "git ls-tree origin/main packages/utils/src/envs/env.mjs",
        "git log --oneline -5 -- packages/utils/src/envs/env.mjs",
        "git log --stat -- .env.staging",
        "git diff --stat -- packages/utils/src/envs/env.mjs",
        "git diff --name-only HEAD~1 -- .env",
        "git grep -l KEY origin/main -- env.mjs",
        "git grep -ci KEY origin/main -- env.mjs",
        "git cat-file -s HEAD:env.mjs",
        "git show --stat HEAD -- env.mjs",
        "git add packages/utils/src/envs/env.mjs",
        "git commit -m \"guard env.mjs\" -- packages/utils/src/envs/env.mjs",
        "git status --short packages/utils/src/envs/env.mjs",
        "git diff origin/main",
        "cat packages/utils/src/types/environment.d.ts",
        "cat src/config/getenv.mjs",
        "cat scripts/dev-env.ts",
        "cat credentials.ts",
        "cat package.json tsconfig.json",
        "cat keys.pem.md",
        "cat luke-pub.pem old-key.pub.pem",
        "openssl x509 -in ca.pem -noout -dates",
        "cat >> notes.md <<'EOF'\nsed -n 1,9p env.mjs 금지\ncat service-account.json 금지\nEOF",
        "gh issue comment 1 --body 'see env.mjs and credential-prod.json'",
        "gh api repos/o/r/contents/README.md",
        "K=$(gh api repos/o/r/contents/android/credential-prod.json --jq .content); echo ${#K}",
        "T=$(sed -n 1,9p packages/utils/src/envs/env.mjs); echo ${#T}",
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
    assert tool_problem({"tool_name": "Read", "tool_input": {"file_path": "/x/app/packages/utils/src/envs/env.mjs"}})
    assert tool_problem({"tool_name": "Read", "tool_input": {"file_path": "/x/old/android/credential-prod.json"}})
    assert tool_problem({"tool_name": "Grep", "tool_input": {"path": "/x/envs/env.mjs", "output_mode": "content"}})
    assert not tool_problem({"tool_name": "Grep", "tool_input": {"path": "/x/envs/env.mjs", "output_mode": "count"}})
    assert not tool_problem({"tool_name": "Read", "tool_input": {"file_path": "/x/types/environment.d.ts"}})
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
