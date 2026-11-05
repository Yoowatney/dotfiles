#!/usr/bin/env python3
"""PreToolUse guard (Claude Code and Codex): keep AI sessions on the agent GCP account.

AI sessions run as an agent service account (production read, staging write) through the
default `munice` gcloud config and ADC files. Yoyoo's own account lives in the `munice-admin`
config and `adc-munice-admin.json`. Two kinds of commands are refused:
  - SELECT: one command picks another identity. Allowed when the session chose admin (`cla`
    sets MUNICE_GCP_ADMIN=1) or Yoyoo's latest typed prompt contains `prod 허용` (that turn only).
  - PERSIST: a change that outlives the command (a login, `config set`, the active config, the
    credential files). Always refused, so an unlocked turn cannot leave admin behind.
terraform#1005.
In the main pane a refused SELECT also runs once when 요유 put ✅ on a Slack reply that holds exactly this command:
slack_approval.py (munice-claude) reads Slack itself, never the text relayed into the pane.

    gcp-account-guard.py --selftest
"""
from __future__ import annotations  # a hook whose python3 is macOS's 3.9 would crash and let everything through

import importlib.util
import json
import os
import re
import signal
import sys
import time

UNLOCK = "prod 허용"
# Yoyoo's account for staging alone: every segment that picks it names only the staging project (api#992).
STAGING_UNLOCK = "staging 허용"
STAGING_IDS = {"miraclenight-dev", "314440450809"}
PROD_IDS = re.compile(r"miraclenight-f06c6|498308011792")
PROJECT_REF = re.compile(r"(?:--project[= ]+|\s-P\s+|CLOUDSDK_CORE_PROJECT=|projects/)['\"]?([\w-]+)")
TOKEN_PRINT = re.compile(r"\bprint-(?:access|identity)-token\b")  # a printed token reaches every project


def said(word: str, prompt: str) -> bool:
    """The word said as a grant, not talked about: `prod 허용이라고 하면` or `prod 허용은 언제` grants nothing."""
    return bool(re.search(rf"(?:^|[\s`'\"(,])`?{re.escape(word)}`?(?:해줘|해요|해|합니다|할게|함)?(?=$|[\s`'\".,!?)])", prompt))
RELAYED = re.compile(r"\s*(<pasted_content[^>]*>\s*)?\[pane-tell:")
SPAWNED = "이 칸은 리드 밑 칸입니다."  # pane-spawn ends every instruction with this: a session's words, and they name the unlock phrase
HOOKS = os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "agent-hooks")
SLACK = os.path.expanduser("~/work/munice/.agents/skills/agent-board/main/slack_approval.py")
SLACK_DEADLINE = 6.0  # both hook configs give 10 s, and a hook that times out lets the command run
# What a Slack ✅ never lifts (condition 4 of the approval): `cla`'s marker, an agent started on 요유's account, a printed token.
BROAD = re.compile(r"\bMUNICE_GCP_ADMIN=|(?:^|[\s;&|(])(?:cla|claude|codex)(?=$|[\s;&|)])")

# ponytail: text patterns over the whole command, heredoc bodies included (a script there runs).
# They stop accidents (a wrong config, a script that grabs the admin ADC), not a session set on
# getting around them: a script can still read the credential store without naming it here.
# Global options may sit between the words of a subcommand: `gcloud config --quiet unset ...`, `firebase -P p login`.
OPTS = r"(?:\s+--?[\w-]+(?:[= ](?!-)(?:'[^']*'|\"[^\"]*\"|\S+))?)*"
SELECT = [  # one command, another identity
    # Picking the config, not mentioning it: notes and commit messages may name it.
    (re.compile(r"(?:--configuration[= ]+|CLOUDSDK_ACTIVE_CONFIG_NAME=)['\"]?munice-admin"), "요유 계정 구성(munice-admin)"),
    # .envrc picks the admin config when this is set, e.g. under `direnv exec`.
    (re.compile(r"\bMUNICE_GCP_ADMIN="), "요유 계정 세션 표시(MUNICE_GCP_ADMIN)"),
    # Setting it in a config is a PERSIST `config set`; `config get` only reads.
    (re.compile(r"--impersonate-service-account\b"), "다른 서비스 계정 대리 실행"),
    (re.compile(r"\bCLOUDSDK_(?:CONFIG|CORE_ACCOUNT|AUTH_[A-Z_]+)="), "gcloud 신원 환경 변수"),
    (re.compile(r"--account[= ]\S+@"), "gcloud --account"),
    # The firebase CLI prefers a token over its login and over ADC.
    (re.compile(r"\bFIREBASE_TOKEN=|\bfirebase\b[^\n;|&]*\s--token\b"), "firebase 토큰"),
]
PERSIST = [  # outlives the command
    (re.compile(rf"\bconfig{OPTS}\s+(?:set|unset){OPTS}\s+(?:core/)?(?:account|auth/\S+)"), "gcloud 계정 설정 변경"),
    (re.compile(rf"\bactivate{OPTS}\s+['\"]?munice-admin"), "활성 gcloud 구성을 요유 계정으로 바꾸기"),
    (re.compile(rf"\bauth{OPTS}\s+(?:login|activate-service-account|application-default{OPTS}\s+login)\b"), "gcloud 로그인"),
    (re.compile(rf"\bfirebase{OPTS}\s+login(?!:list\b)"), "firebase 로그인"),  # login:use, login:ci, login:add too
    # Reading one leaks a login into the transcript, writing one swaps an identity for good.
    (re.compile(r"credentials\.db|access_tokens\.db|legacy_credentials|application_default_credentials\.json"
                r"|adc-munice|\.config/gcloud/configurations|configstore/firebase-tools\.json"), "자격 증명 파일"),
    # The used marks of Slack approvals: one removed would let its ✅ run the command again.
    (re.compile(r"\.local/state/gcp-account-guard"), "Slack 승인 사용 기록"),
]
# Service account keys never expire and Yoyoo's account can lift the org policy that blocks them, so an AI
# session never makes, uploads or re-enables one, nor changes an org policy, even when unlocked (terraform#1040).
# Listing, disabling and deleting keys stay allowed. Yoyoo runs a needed key with `!` in the terminal.
POSTS = (r"(?=.*(?:-X\s*['\"]?(?:POST|PATCH|DELETE)|--request[= ]['\"]?(?:POST|PATCH|DELETE)"
         r"|\s-d(?:\s|['\"@{])|--data|--json))")
SA = r"serviceAccounts/[^\s/'\"]+/keys"
KEYS = [  # matched per shell segment, after joining `\` continuations
    (re.compile(rf"\bservice-accounts{OPTS}\s+keys{OPTS}\s+(?:create|upload|enable)\b"), "서비스 계정 키 만들기"),
    (re.compile(rf"\borg-policies{OPTS}\s+(?:disable-enforce|enable-enforce|set-policy|allow|deny|delete|reset|create"
                r"|update|set-custom-constraint|delete-custom-constraint)\b"), "조직 정책 바꾸기"),
    # POST to the keys collection creates; /keys/<id> (delete) and /keys/<id>:disable stay allowed.
    (re.compile(rf"{SA}:upload|{SA}/[^\s/:'\"]+:enable|{POSTS}.*{SA}(?![/\w:])"), "서비스 계정 키 API"),
    # Client libraries. Lower-case `create...` keeps log filters on `v1.CreateServiceAccountKey` readable.
    (re.compile(r"\.keys(?:\(\))?\s*\.(?:create|upload|enable)\(|\b(?:create|upload|enable)"
                r"(?:_service_account_key|ServiceAccountKey)\b"), "서비스 계정 키 API"),
    (re.compile(r"[.:](?:set|clear)OrgPolicy\b|\bOrgPolicyClient\b|" + POSTS + r".*orgpolicy\.googleapis\.com"),
     "조직 정책 API"),
]
# A commit message, a PR body or a search may name a key command; such a segment only carries text.
# No echo/printf (they feed `| sh`), and a segment with $( or a backtick still runs what it holds.
# ponytail: a heredoc commit body is still read line by line, so commit long messages with -F <file>.
TEXT_ONLY = re.compile(r"\s*(?:\w+=\S*\s+)*(?:git\s+(?:commit|tag|grep|log|show)|gh\s+(?:pr|issue|release)\s+"
                       r"(?:create|edit|comment|view)|rg|grep)\b")
SUBST = re.compile(r"\$\(|`")  # checked after dropping '...' strings, where the shell substitutes nothing
SEGMENTS = re.compile(r"&&|\|\||[;|\n]")
# A key resource written into a .tf file lands its private key in state and makes a key on merge.
KEY_TF = re.compile(r'resource\s+"google_service_account_key"|"google_service_account_key"\s*:')
TF_FILE = re.compile(r"\.tf(?:\.json)?$", re.M)
TF_SHELL_WRITE = re.compile(r"(?:>>?|\btee(?:\s+-a)?)\s*['\"]?[^\s;|&'\"]*\.tf(?:\.json)?\b")
# Pointing ADC at another file swaps the identity of every library call; gomgom keeps its own file.
ADC_ASSIGN = re.compile(r"\bGOOGLE_APPLICATION_CREDENTIALS=(\S*)")
CONFIG_DIRS = re.compile(r"/\.config/gcloud(?:/|$)|configstore/firebase-tools\.json|/\.local/state/gcp-account-guard(?:/|$)")
WRITERS = {"Write", "Edit", "MultiEdit", "NotebookEdit", "apply_patch"}
READERS = {"Read", "Grep"}  # a credential file read into the transcript leaks the login


def bash_problem(cmd: str) -> tuple[str, bool | str] | None:
    """(what, persists) for the first refused part of a command; persists is "key" for KEYS."""
    flat = cmd.replace("\\\n", " ")
    for segment in SEGMENTS.split(flat):
        if TEXT_ONLY.match(segment) and not SUBST.search(re.sub(r"'[^']*'", "", segment)):
            continue
        for pattern, what in KEYS:
            if pattern.search(segment):
                return what, "key"
    if KEY_TF.search(flat) and TF_SHELL_WRITE.search(flat):
        return "terraform 서비스 계정 키 리소스", "key"
    # The file an ADC assignment names is a SELECT, not a read of the credential store.
    for pattern, what in PERSIST:
        if pattern.search(ADC_ASSIGN.sub("", cmd)):
            return what, True
    for pattern, what in SELECT:
        if pattern.search(cmd):
            return what, False
    for m in ADC_ASSIGN.finditer(cmd):  # gomgom's own file, or the agent's default file named outright
        if not re.search(r"(?:gomgom\S*|/adc-munice\.json['\"]?)$", m.group(1)):
            return "GOOGLE_APPLICATION_CREDENTIALS 바꾸기", False
    return None


def staging_only(cmd: str) -> bool:
    """True when each segment that picks another identity names the staging project and nothing else."""
    if PROD_IDS.search(cmd) or TOKEN_PRINT.search(cmd):
        return False
    for segment in SEGMENTS.split(cmd.replace("\\\n", " ")):
        if not ADC_ASSIGN.search(segment) and not any(p.search(segment) for p, _ in SELECT):
            continue
        refs = PROJECT_REF.findall(segment)
        if not refs or any(r not in STAGING_IDS for r in refs):
            return False
    return True


PATCH_PATHS = re.compile(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+)$", re.M)


def write_targets(tool: str, inp: dict, cwd: str) -> str:
    """Absolute paths a write lands on; the content may quote config paths freely."""
    if tool == "apply_patch":
        paths = PATCH_PATHS.findall(str(inp.get("command") or inp.get("input") or inp.get("patch") or ""))
    else:
        paths = [str(inp.get("file_path") or inp.get("notebook_path") or "")]
    return "\n".join(os.path.normpath(os.path.join(cwd, os.path.expanduser(p.strip()))) for p in paths if p.strip())


def write_problem(text: str) -> tuple[str, bool] | None:
    return ("gcloud·firebase 설정 파일 쓰기", True) if CONFIG_DIRS.search(text) else None


def written_text(tool: str, inp: dict) -> str:
    """What a write adds: Write content, Edit/MultiEdit new text, a Codex patch's `+` lines."""
    if tool == "apply_patch":
        patch = str(inp.get("command") or inp.get("input") or inp.get("patch") or "")
        return "\n".join(line[1:] for line in patch.splitlines() if line.startswith("+"))
    parts = [inp.get("content"), inp.get("new_string")]
    parts += [e.get("new_string") for e in inp.get("edits") or [] if isinstance(e, dict)]
    return "\n".join(str(p) for p in parts if p)


def latest_prompt(transcript_path: str) -> str:
    """Text of the last prompt a person typed: Claude human-origin lines, Codex user messages."""
    last = ""
    try:
        f = open(transcript_path, encoding="utf-8", errors="replace")
    except OSError:
        return ""
    with f:
        for line in f:
            if '"human"' not in line and '"role": "user"' not in line and '"role":"user"' not in line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("type") == "user" and (d.get("origin") or {}).get("kind") == "human":
                content = (d.get("message") or {}).get("content")
            elif d.get("type") == "response_item" and (d.get("payload") or {}).get("role") == "user":
                content = d["payload"].get("content")
            else:
                continue
            if isinstance(content, list):
                content = "\n".join(p.get("text", "") for p in content if isinstance(p, dict))
            text = content if isinstance(content, str) else ""
            # Codex sends its instruction files as user messages too; a person never types these.
            if not text.lstrip().startswith(("# AGENTS.md", "<environment_context>", "<user_instructions>", "<hook_prompt", "<user_action>")):
                # pane-tell lands in the input box, so it reads as human origin. It is another session's words and unlocks nothing.
                last = "" if RELAYED.match(text) or SPAWNED in text else text
    return last


def tool_problem(payload: dict, env: dict) -> str | None:
    tool, inp = payload.get("tool_name"), payload.get("tool_input") or {}
    if tool in ("Bash", "Monitor"):
        hit = bash_problem(inp.get("command") or "")
    elif tool in WRITERS:
        targets = write_targets(tool, inp, str(payload.get("cwd") or os.getcwd()))
        hit = write_problem(targets)
        if not hit and TF_FILE.search(targets) and KEY_TF.search(written_text(tool, inp)):
            hit = ("terraform 서비스 계정 키 리소스", "key")
    elif tool in READERS:
        target = str(inp.get("file_path") or inp.get("path") or "")
        cwd = str(payload.get("cwd") or os.getcwd())
        hit = target and CONFIG_DIRS.search(os.path.normpath(os.path.join(cwd, os.path.expanduser(target))))
        hit = ("gcloud·firebase 자격 증명 파일 읽기", True) if hit else None
    else:
        return None
    if not hit:
        return None
    what, persists = hit
    if persists == "key":
        return (f"{what}: AI 세션은 서비스 계정 키를 만들거나 다시 켜지 않고 조직 정책도 바꾸지 않습니다(`{UNLOCK}`이나 "
                "`cla`로도 안 풀림). 키는 만료가 없습니다. GCP 밖 기계라면 먼저 X.509 인증서 방식(terraform#1028)을 "
                "쓸 것. 그래도 키가 필요하면 무엇을 왜 만드는지 한국어 한 문장으로 요유에게 따로 묻고, 요유가 직접 "
                "터미널에서 `!`로 실행한다. terraform#1040")
    if persists:
        return (f"{what}: 명령이 끝난 뒤에도 남는 신원 변경이라 AI 세션에서는 하지 않습니다(`{UNLOCK}`이나 `cla`로도 "
                "안 풀림). 필요하면 요유가 직접 터미널에서 할 것(예: `! gcloud auth login`). 재로그인 뒤에는 "
                "use-agent-account.sh를 다시 돌린다. terraform#1005")
    if env.get("MUNICE_GCP_ADMIN") == "1":
        return None
    # Job-opened tabs get their prompts typed in by a job (ambient pastes Slack threads), so the word proves nothing.
    auto = env.get("MUNICE_AUTO_DISPATCH") == "1" or env.get("MUNICE_AMBIENT") == "1"
    prompt = "" if auto else latest_prompt(payload.get("transcript_path") or "")
    if said(UNLOCK, prompt):
        return None
    if said(STAGING_UNLOCK, prompt):
        if tool in ("Bash", "Monitor") and staging_only(inp.get("command") or ""):
            return None
        return (f"{what}: `{STAGING_UNLOCK}` 턴에서는 요유 계정을 고르는 명령마다 staging 프로젝트만 적어야 합니다"
                "(`--project miraclenight-dev`, firebase는 `-P miraclenight-dev`). prod나 다른 프로젝트를 가리키거나 "
                f"토큰을 꺼내는 명령은 안 됩니다. prod 쓰기면 요유에게 `{UNLOCK}`을 따로 받을 것. api#992")
    if auto:
        return (f"{what}: 자동 탭은 에이전트 계정(staging 쓰기, prod 읽기)으로만 GCP를 쓰고, 이 잠금은 풀 수 없습니다. "
                "prod 쓰기가 필요하면 무엇을 왜 바꾸는지 보드 항목에 적고 owner를 yoyoo로 넘길 것. terraform#1005")
    return (f"{what}: AI 세션은 에이전트 계정(staging 쓰기, prod 읽기)으로만 GCP를 씁니다. prod 쓰기가 꼭 필요하면 "
            f"여기서 멈추고, 무엇을 왜 바꾸는지 적은 뒤 요유에게 '`{UNLOCK}`이라고 입력하면 이번 턴만 요유 계정으로 "
            f"할 수 있다'고 말할 것. staging만 바꾸는 일이면 `{STAGING_UNLOCK}`을 받을 것(staging 프로젝트를 적은 명령만 풀림). 리드 밑 칸이면 요유가 이 칸을 보지 않으니 보고로 리드에게 넘길 것: 리드가 자기 칸에서 "
            "요유에게 묻고 직접 실행함. 처음부터 prod 작업 세션이면 요유가 `cla`로 띄움. terraform#1005")


def load(path: str):
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location(os.path.basename(path).replace("-", "_")[:-3], path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rider(name: str, hook_input: dict) -> str | None:
    """A check that rides on this hook: see pane-fit-guard.py for why. It can never change a GCP verdict."""
    try:
        return load(os.path.join(HOOKS, name)).check(hook_input, dict(os.environ))
    except Exception:
        return None


def riders(hook_input: dict) -> str | None:
    return rider("pane-fit-guard.py", hook_input) or rider("job-host-guard.py", hook_input) or rider("fix-round-guard.py", hook_input)


def main_pane(env: dict) -> bool:
    """This session runs in the main pane. A moved pane keeps an old HERDR_PANE_ID, which only `pane get` still resolves."""
    try:
        peers = load(os.path.join(HOOKS, "herdr_peers.py"))
        terminal = (peers.herdr(env, "pane", "get", env.get("HERDR_PANE_ID") or "-").get("pane") or {}).get("terminal_id")
        return bool(terminal) and terminal == peers.main_terminal(env)
    except Exception:
        return False


def slack_liftable(payload: dict, env: dict) -> bool:
    """A refusal a Slack ✅ may lift: one Bash command that picks another identity, outside job tabs, nothing broad."""
    if payload.get("tool_name") != "Bash" or env.get("MUNICE_AUTO_DISPATCH") == "1" or env.get("MUNICE_AMBIENT") == "1":
        return False
    cmd = (payload.get("tool_input") or {}).get("command") or ""
    hit = bash_problem(cmd)
    return bool(hit) and hit[1] is False and not BROAD.search(cmd) and not TOKEN_PRINT.search(cmd)


class Deadline(BaseException):
    """Not an Exception: code under the alarm that catches errors (an OSError, say) must not swallow it."""


def slack_approval(cmd: str, deadline: float = SLACK_DEADLINE, path: str = SLACK):
    """(consume, "") when 요유 approved exactly this command in Slack, else (None, why). Done or refused within the deadline."""
    if not os.path.isfile(path):
        return None, "Slack 승인 모듈이 이 Mac에 없음"

    def expire(*_):
        raise Deadline()
    old = signal.signal(signal.SIGALRM, expire)
    signal.setitimer(signal.ITIMER_REAL, deadline)
    try:
        module = load(path)
        ts, why = module.find(cmd, time.time())
        signal.setitimer(signal.ITIMER_REAL, 0)
        return ((lambda: module.consume(ts, cmd)) if ts else None), why
    except Deadline:
        return None, f"Slack을 {deadline:g}초 안에 읽지 못함"
    except Exception as e:
        return None, f"Slack을 읽지 못함({type(e).__name__})"
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def hook(hook_input: dict, env: dict, riders, approvals, is_main) -> str | None:
    """The whole PreToolUse verdict. A Slack approval is used up only once nothing else refuses the call: pane-fit-guard asks
    the main pane once a message, and its repeat must still find the approval."""
    reason, use = tool_problem(hook_input, env), None
    if reason and slack_liftable(hook_input, env) and is_main(env):
        try:
            use, why = approvals((hook_input.get("tool_input") or {}).get("command") or "")
        except BaseException as e:  # a late alarm included: this path never crashes open
            use, why = None, f"Slack을 읽지 못함({type(e).__name__})"
        reason = None if use else (
            f"{reason}\n메인 칸은 Slack ✅로도 이 명령 하나를 한 번 실행할 수 있습니다(메인 칸 스킬 'Steps only 요유 can unlock'). "
            f"지금 Slack: {why}")
    if reason:
        return f"gcp-account-guard: {reason}"
    reason = riders(hook_input)
    if reason or not use:
        return reason
    return None if use() else "gcp-account-guard: 이 Slack 승인은 방금 다른 실행이 썼습니다. 다시 하려면 새 요청에 ✅가 필요합니다."


def selftest():
    import tempfile

    deny = [
        "gcloud --configuration=munice-admin firestore databases list",
        "CLOUDSDK_ACTIVE_CONFIG_NAME=munice-admin gcloud run services list",
        "GOOGLE_APPLICATION_CREDENTIALS=$HOME/.config/gcloud/adc-munice-admin.json node fix.js",
        "GOOGLE_APPLICATION_CREDENTIALS=/tmp/key.json npm run start:prod",
        "export GOOGLE_APPLICATION_CREDENTIALS=~/Downloads/sa.json",
        "gcloud run services list --impersonate-service-account=api@example-prod.iam.gserviceaccount.com",
        "gcloud config unset auth/impersonate_service_account",
        "gcloud config set account me@example.com",
        "gcloud config configurations activate munice-admin",
        "gcloud auth login",
        "gcloud auth application-default login",
        "gcloud auth activate-service-account --key-file=k.json",
        "firebase login --reauth",
        "firebase login:use other@example.com",
        "firebase login:ci",
        "CLOUDSDK_CONFIG=/tmp/gc gcloud auth list",
        "gcloud --account=me@example.com secrets list",
        "cp ~/.config/gcloud/adc-munice-admin.json ~/.config/gcloud/adc-munice.json",
        "sqlite3 ~/.config/gcloud/credentials.db .dump",
        "TOKEN=$(gcloud --configuration munice-admin auth print-access-token)",
        "MUNICE_GCP_ADMIN=1 direnv exec . gcloud run services update api",
        "export CLOUDSDK_ACTIVE_CONFIG_NAME='munice-admin'",
        # global options between the words, firebase tokens (shared-library#27 Codex review)
        "gcloud config --quiet unset auth/impersonate_service_account",
        "gcloud config set --quiet account me@example.com",
        "gcloud auth --quiet login",
        "gcloud auth application-default --quiet login",
        "gcloud config configurations activate --quiet munice-admin",
        "firebase --project example-prod login --reauth",
        "FIREBASE_TOKEN=abc firebase deploy --only firestore:rules",
        "firebase deploy --only firestore:rules --token abc",
    ]
    allow = [
        "gcloud run services list --project example-prod",
        "gcloud logging read 'severity>=ERROR' --project=example-prod --limit 5",
        "bq query --nouse_legacy_sql 'SELECT 1'",
        "gcloud config configurations activate gomgom",
        "GOOGLE_APPLICATION_CREDENTIALS=$HOME/.config/gcloud/adc-gomgom.json node gomgom.js",
        "gcloud config list",
        "gcloud config get auth/impersonate_service_account --configuration=munice",
        "gcloud config --quiet set project example-staging",
        "gcloud auth list",
        "firebase projects:list",
        "firebase login:list",
        "firebase --project example-prod login:list",
        "firebase deploy -m 'fix login screen'",
        "ls ~/.config/gcloud",
        "grep -n prod AGENTS.md",
        "git commit -m 'docs: cla keeps the munice-admin config'",
    ]
    for c in deny:
        assert bash_problem(c), f"should deny: {c}"
    for c in allow:
        assert bash_problem(c) is None, f"should allow: {c} -> {bash_problem(c)}"

    assert write_problem(os.path.expanduser("~/.config/gcloud/configurations/config_munice"))
    assert write_problem("/Users/yoyoo/.config/configstore/firebase-tools.json")
    assert write_problem("/Users/yoyoo/work/munice/notes.md") is None

    def read(tool: str, target: str, cwd: str = "/tmp") -> str | None:
        key = "file_path" if tool == "Read" else "path"
        return tool_problem({"tool_name": tool, "tool_input": {key: target}, "cwd": cwd}, {"MUNICE_GCP_ADMIN": "1"})
    # a credential file read into the transcript leaks the login, even in a cla session
    assert read("Read", "~/.config/gcloud/adc-munice-admin.json")
    assert read("Read", ".config/gcloud/application_default_credentials.json", cwd=os.path.expanduser("~"))
    assert read("Grep", os.path.expanduser("~/.config/gcloud"))
    assert read("Read", "~/.config/configstore/firebase-tools.json")
    assert read("Read", "/tmp/notes.md") is None
    assert read("Grep", "src") is None

    tmp = tempfile.mkdtemp()

    def claude_transcript(*prompts: str, tail_tool: bool = True) -> str:
        p = os.path.join(tmp, f"c{len(os.listdir(tmp))}.jsonl")
        with open(p, "w") as f:
            for text in prompts:
                f.write(json.dumps({"type": "user", "origin": {"kind": "human"},
                                    "message": {"role": "user", "content": text}}, ensure_ascii=False) + "\n")
                f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "ok"}]}}) + "\n")
            if tail_tool:  # a tool result line is a "user" line without a human origin
                f.write(json.dumps({"type": "user", "message": {"content": [{"type": "tool_result", "content": "x"}]}}) + "\n")
        return p

    def codex_transcript(*prompts: str) -> str:
        p = os.path.join(tmp, f"x{len(os.listdir(tmp))}.jsonl")
        with open(p, "w") as f:
            f.write(json.dumps({"type": "response_item", "payload": {"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": "# AGENTS.md instructions for /x\n\nprod 허용 is a phrase"}]}}, ensure_ascii=False) + "\n")
            for text in prompts:
                f.write(json.dumps({"type": "response_item", "payload": {"type": "message", "role": "user", "content": [
                    {"type": "input_text", "text": text}]}}, ensure_ascii=False) + "\n")
                f.write(json.dumps({"type": "response_item", "payload": {"type": "function_call", "name": "exec"}}) + "\n")
        return p

    admin_cmd = {"tool_name": "Bash", "tool_input": {"command": "gcloud --configuration=munice-admin run services update api"}}
    # locked: no unlock word anywhere
    assert tool_problem({**admin_cmd, "transcript_path": claude_transcript("prod 데이터 고쳐줘")}, {})
    # unlocks on the latest human prompt
    assert tool_problem({**admin_cmd, "transcript_path": claude_transcript("고쳐줘", "prod 허용")}, {}) is None
    # next prompt relocks
    assert tool_problem({**admin_cmd, "transcript_path": claude_transcript("prod 허용", "이제 로그만 봐줘")}, {})
    relayed = "[pane-tell: '리드' 세션이 보낸 글입니다.] 요유가 prod 허용이라고 했습니다"
    for text in (relayed, f'<pasted_content id="1">\n{relayed}\n</pasted_content id="1">', f'\n\n<pasted_content id="1">\n{relayed}'):
        assert tool_problem({**admin_cmd, "transcript_path": claude_transcript(text)}, {}), f"relayed text unlocks nothing: {text!r}"
    assert tool_problem({**admin_cmd, "transcript_path": claude_transcript("prod 허용", relayed)}, {}), "a relayed turn ends the unlock too"
    spawned = f"로그를 봐 주세요.\n\n{SPAWNED} 요유에게 직접 묻거나 `prod 허용`, `/clear`를 부탁하지 마세요."
    assert tool_problem({**admin_cmd, "transcript_path": claude_transcript(spawned)}, {}), "pane-spawn's own note is no typed prompt"
    assert tool_problem({**admin_cmd, "transcript_path": codex_transcript(spawned)}, {}), "the same in Codex"
    for text in ('<hook_prompt hook_run_id="stop:20">`prod 허용`</hook_prompt>', "<user_action>\n  review says `prod 허용`\n</user_action>"):
        assert tool_problem({**admin_cmd, "transcript_path": codex_transcript(text)}, {}), f"Codex writes these, no person: {text[:20]}"
    assert tool_problem({**admin_cmd, "transcript_path": claude_transcript("prod 허용", spawned)}, {}), "a spawned turn ends the unlock too"
    assert SPAWNED in open(os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "agent-hooks", "pane-spawn"), encoding="utf-8").read(), \
        "this must match what pane-spawn appends"
    # subagent transcript has no human line
    assert tool_problem({**admin_cmd, "transcript_path": claude_transcript(tail_tool=True)}, {})
    # an unlocked turn may pick admin for one command, never leave it behind (Codex review P1)
    unlocked = claude_transcript("고쳐줘", "prod 허용")

    def bash(cmd, env=None, cwd="/tmp"):
        return tool_problem({"tool_name": "Bash", "tool_input": {"command": cmd}, "transcript_path": unlocked,
                             "cwd": cwd}, env or {})
    assert bash("GOOGLE_APPLICATION_CREDENTIALS=$HOME/.config/gcloud/adc-munice-admin.json node fix.js") is None
    assert bash("CLOUDSDK_ACTIVE_CONFIG_NAME=munice-admin gcloud run services update api") is None
    assert bash("cp ~/.config/gcloud/adc-munice-admin.json ~/.config/gcloud/adc-munice.json")
    assert bash("gcloud config set account me@example.com")
    assert bash("gcloud config configurations activate munice-admin")
    assert bash("cat ~/.config/gcloud/adc-munice-admin.json")
    # cla picks admin per command but cannot persist either
    assert bash("gcloud --configuration=munice-admin run services list", {"MUNICE_GCP_ADMIN": "1"}) is None
    assert bash("gcloud auth login", {"MUNICE_GCP_ADMIN": "1"})
    # the agent's own default file named outright is not a switch
    assert bash_problem("GOOGLE_APPLICATION_CREDENTIALS=~/.config/gcloud/adc-munice.json node job.js") is None
    # relative write paths resolve against cwd (Codex review P2)
    home = os.path.expanduser("~")
    assert tool_problem({"tool_name": "Edit", "tool_input": {"file_path": ".config/gcloud/configurations/config_munice"},
                         "transcript_path": unlocked, "cwd": home}, {})
    assert tool_problem({"tool_name": "apply_patch", "tool_input": {"command": "*** Begin Patch\n*** Update File: "
                         ".config/gcloud/adc-munice.json\n@@\n-a\n+b\n*** End Patch"}, "transcript_path": unlocked,
                         "cwd": home}, {})
    assert tool_problem({"tool_name": "Edit", "tool_input": {"file_path": "notes.md"}, "transcript_path": unlocked,
                         "cwd": home}, {}) is None
    # `staging 허용`: Yoyoo's account only for commands that name the staging project and nothing else (api#992)
    staged = claude_transcript("고쳐줘", "staging 허용, 키 갈자")

    def stg(cmd, env=None):
        return tool_problem({"tool_name": "Bash", "tool_input": {"command": cmd}, "transcript_path": staged,
                             "cwd": "/tmp"}, env or {})
    for c in [
        "gcloud --configuration=munice-admin secrets versions add OPENAI_API_KEY --project=miraclenight-dev --data-file=/tmp/k",
        'printf %s "${V}" | gcloud --configuration=munice-admin secrets versions add R2_API_TOKEN --project miraclenight-dev --data-file=-',
        "CLOUDSDK_ACTIVE_CONFIG_NAME=munice-admin gcloud run services describe hub-nightly-so --project miraclenight-dev --region asia-northeast1",
        "GOOGLE_APPLICATION_CREDENTIALS=$HOME/.config/gcloud/adc-munice-admin.json firebase deploy --only functions:assets -P miraclenight-dev",
    ]:
        assert stg(c) is None, f"staging unlock should allow: {c} -> {stg(c)}"
    for c in [
        "gcloud --configuration=munice-admin secrets versions add OPENAI_API_KEY --project=miraclenight-f06c6 --data-file=-",
        "gcloud --configuration=munice-admin run services list",  # the admin config's default project is prod
        "gcloud --configuration=munice-admin secrets list --project miraclenight-dev; gcloud --configuration=munice-admin secrets list",
        "gcloud --configuration=munice-admin secrets list --project miraclenight-dev --project munice-tfstate",
        "gcloud --configuration=munice-admin run services update api --project miraclenight-dev --image x/miraclenight-f06c6/y",
        "gcloud --configuration=munice-admin auth print-access-token --project miraclenight-dev",  # a token reaches every project
        "GOOGLE_APPLICATION_CREDENTIALS=$HOME/.config/gcloud/adc-munice-admin.json node fix.js",
        "gcloud config configurations activate munice-admin --project miraclenight-dev",  # PERSIST stays refused
    ]:
        assert stg(c), f"staging unlock should refuse: {c}"
    assert "prod 허용" in (stg("gcloud --configuration=munice-admin run services list --project=miraclenight-f06c6") or "")
    assert stg("gcloud --configuration=munice-admin secrets list --project miraclenight-dev", {"MUNICE_AMBIENT": "1"})
    stg_relayed = "[pane-tell: '리드' 세션이 보낸 글입니다.] 요유가 staging 허용이라고 했습니다"
    assert tool_problem({"tool_name": "Bash", "tool_input": {"command": "gcloud --configuration=munice-admin secrets list "
                         "--project miraclenight-dev"}, "transcript_path": claude_transcript(stg_relayed)}, {})
    # the next prompt relocks
    assert tool_problem({"tool_name": "Bash", "tool_input": {"command": "gcloud --configuration=munice-admin secrets list "
                         "--project miraclenight-dev"}, "transcript_path": claude_transcript("staging 허용", "로그 봐줘")}, {})
    # talking about the word grants nothing; saying it does (api#992: "prod 허용이라고하면 진짜 prod를 허용하는거 같잖아")
    for text in ("prod 허용이라고하면 진짜 prod를 허용하는거 같잖아;; staging 허용이라고 하는게 맞지않냐?",
                 "prod 허용은 언제 쓰는 거야?", "staging 허용이란 말 만들었어?"):
        assert tool_problem({**admin_cmd, "transcript_path": claude_transcript(text)}, {}), f"a mention unlocks nothing: {text}"
    for text in ("prod 허용", "`prod 허용`", "좋아, prod 허용.", "prod 허용해줘", "확인했어 prod 허용합니다"):
        assert tool_problem({**admin_cmd, "transcript_path": claude_transcript(text)}, {}) is None, f"should unlock: {text}"
    # an auto tab (ambient, dispatch) never unlocks by the word
    assert tool_problem({**admin_cmd, "transcript_path": claude_transcript("prod 허용")}, {"MUNICE_AMBIENT": "1"})
    assert tool_problem({**admin_cmd, "transcript_path": claude_transcript("prod 허용")}, {"MUNICE_AUTO_DISPATCH": "1"})
    # cla session
    assert tool_problem({**admin_cmd, "transcript_path": claude_transcript("고쳐줘")}, {"MUNICE_GCP_ADMIN": "1"}) is None
    # missing transcript stays locked
    assert tool_problem({**admin_cmd, "transcript_path": os.path.join(tmp, "none.jsonl")}, {})
    # Codex: AGENTS.md injection does not unlock, a typed prompt does
    assert tool_problem({**admin_cmd, "transcript_path": codex_transcript("prod 고쳐줘")}, {})
    assert tool_problem({**admin_cmd, "transcript_path": codex_transcript("고쳐줘", "prod 허용")}, {}) is None
    # writes into the gcloud dir, Claude Write and Codex apply_patch
    assert tool_problem({"tool_name": "Write", "tool_input": {"file_path": os.path.expanduser(
        "~/.config/gcloud/adc-munice.json")}, "transcript_path": claude_transcript("x")}, {})
    assert tool_problem({"tool_name": "apply_patch", "tool_input": {"command": "*** Begin Patch\n*** Update File: "
        + os.path.expanduser("~/.config/gcloud/configurations/config_munice") + "\n@@\n-a\n+b\n*** End Patch"},
        "transcript_path": codex_transcript("x")}, {})
    # a note whose content quotes a config path is not a write into the config dir
    assert tool_problem({"tool_name": "Write", "tool_input": {"file_path": "/tmp/notes.md", "content":
        "backup: ~/.config/gcloud/adc-munice.json.bak"}, "transcript_path": claude_transcript("x")}, {}) is None
    assert tool_problem({"tool_name": "apply_patch", "tool_input": {"command": "*** Begin Patch\n*** Add File: notes.md\n"
        "+see ~/.config/gcloud/configurations\n*** End Patch"}, "transcript_path": codex_transcript("x")}, {}) is None
    assert tool_problem({"tool_name": "Bash", "tool_input": {"command": "gcloud run services list"},
                         "transcript_path": claude_transcript("x")}, {}) is None
    assert tool_problem({"tool_name": "Monitor", "tool_input": {"command": "gcloud auth login"},
                         "transcript_path": claude_transcript("x")}, {})
    # service account keys and org policy: never from an AI session, unlocked turn and cla included (terraform#1040)
    key_deny = [
        "gcloud iam service-accounts keys create k.json --iam-account=a@example-prod.iam.gserviceaccount.com",
        "gcloud --configuration=munice-admin iam service-accounts keys create k.json --iam-account=a@x",
        "gcloud iam --quiet service-accounts keys upload pub.pem --iam-account=a@x",
        "gcloud iam service-accounts keys enable 58992b45 --iam-account=a@x",
        "gcloud beta iam service-accounts keys create k.json --iam-account=a@x",
        "gcloud resource-manager org-policies disable-enforce iam.disableServiceAccountKeyCreation --project=p",
        "gcloud resource-manager org-policies set-policy policy.yaml --project=p",
        "gcloud org-policies reset iam.disableServiceAccountKeyCreation --project=p",
        "gcloud org-policies set-policy policy.yaml",
        "gcloud org-policies delete iam.disableServiceAccountKeyCreation --project=p",
        "curl -X POST https://iam.googleapis.com/v1/projects/p/serviceAccounts/a@x/keys:upload -d @k",
        "curl -X POST -H 'Authorization: Bearer x' https://iam.googleapis.com/v1/projects/-/serviceAccounts/a@x/keys",
        "python3 -c 'svc.projects().serviceAccounts().keys().create(name=n, body={}).execute()'",
        # line continuations, --json, -XPOST, a heredoc that writes a key resource (Codex review)
        "curl -X POST \\\n  -H 'Authorization: Bearer x' \\\n  https://iam.googleapis.com/v1/projects/-/serviceAccounts/a@x/keys",
        "curl --json '{}' https://iam.googleapis.com/v1/projects/-/serviceAccounts/a@x/keys",
        "curl -XPOST https://iam.googleapis.com/v1/projects/-/serviceAccounts/a@x/keys/58992b45:enable",
        "python3 -c 'iam_admin_v1.IAMClient().create_service_account_key(request=r)'",
        'cat > key.tf <<TF\nresource "google_service_account_key" "k" {\n  service_account_id = x\n}\nTF',
        'tee -a google-cloud-platform/staging/k.tf <<TF\nresource "google_service_account_key" "k" {}\nTF',
        # body flags written tight, a quoted method, Node and other clients, commands behind shell words (Claude review)
        "curl -d@body.json https://iam.googleapis.com/v1/projects/-/serviceAccounts/a@x/keys",
        "curl -d'{}' https://iam.googleapis.com/v1/projects/-/serviceAccounts/a@x/keys",
        "curl -X 'POST' https://iam.googleapis.com/v1/projects/-/serviceAccounts/a@x/keys",
        "node -e 'await iam.projects.serviceAccounts.keys.create({name})'",
        "node -e 'await client.createServiceAccountKey({name})'",
        "python3 -c 'svc.projects().serviceAccounts().keys().upload(name=n, body=b)'",
        "python3 -c 'svc.projects().serviceAccounts().keys().enable(name=n)'",
        "python3 -c 'crm.projects().setOrgPolicy(resource=r, body=b).execute()'",
        "python3 -c 'orgpolicy_v2.OrgPolicyClient().update_policy(policy=p)'",
        "if true; then gcloud iam service-accounts keys create k.json --iam-account=a@x; fi",
        "timeout 30 gcloud iam service-accounts keys create k.json --iam-account=a@x",
        "gcloud iam service-accounts keys --format 'value(name, keyType)' create k.json --iam-account=a@x",
        # text commands that still run what they carry: a pipe to a shell, a command substitution
        "echo 'gcloud iam service-accounts keys create k.json --iam-account=a@x' | sh",
        "printf 'gcloud iam service-accounts keys create k.json --iam-account=a@x\\n' | bash",
        'git commit --allow-empty -m "$(gcloud iam service-accounts keys create k.json --iam-account=a@x)"',
        "gh pr comment 1 --body `gcloud iam service-accounts keys create k.json --iam-account=a@x`",
    ]
    for c in key_deny:
        for env in ({}, {"MUNICE_GCP_ADMIN": "1"}, {"MUNICE_AMBIENT": "1"}):
            reason = bash(c, env)
            assert reason and "terraform#1040" in reason, f"key command should be denied: {c} {env} -> {reason}"
    key_allow = [
        "gcloud iam service-accounts keys list --iam-account=a@x --managed-by=user",
        "gcloud iam service-accounts keys disable 58992b45 --iam-account=a@x",
        "gcloud iam service-accounts keys delete 58992b45 --iam-account=a@x --quiet",
        "gcloud resource-manager org-policies describe iam.disableServiceAccountKeyCreation --project=p --effective",
        "gcloud org-policies describe iam.disableServiceAccountKeyCreation --project=p",
        "gcloud logging read 'protoPayload.methodName=\"google.iam.admin.v1.CreateServiceAccountKey\"' --project=p",
        "curl -s https://iam.googleapis.com/v1/projects/-/serviceAccounts/a@x/keys",
        # disabling and deleting through REST stay allowed (Codex review)
        "curl -X DELETE https://iam.googleapis.com/v1/projects/-/serviceAccounts/a@x/keys/58992b45",
        "curl -X POST https://iam.googleapis.com/v1/projects/-/serviceAccounts/a@x/keys/58992b45:disable -d '{}'",
        "git grep -n 'resource \"google_service_account_key\"'",
        "git commit -m 'docs: never add a google_service_account_key resource'",
        # text that only names a key command: commit messages, PR bodies, searches (Claude review)
        "git commit -m 'fix: refuse gcloud iam service-accounts keys create in AI sessions'",
        "gh pr create --title t --body 'org-policies disable-enforce is refused now'",
        "gh pr create --title t --body 'Blocks `gcloud iam service-accounts keys create` in AI sessions'",
        "rg -n 'service-accounts keys create' ~/.dotfiles",
        "curl -s https://iam.googleapis.com/v1/projects/-/serviceAccounts/a@x/keys | cut -d '/' -f6",
        "gcloud logging read 'protoPayload.resourceName:\"serviceAccounts/x/keys\"' --project=p | cut -d 'T' -f1",
        "gcloud logging read 'protoPayload.methodName=\"SetOrgPolicy\"' --project=p",
    ]
    for c in key_allow:
        assert bash(c) is None, f"should allow: {c} -> {bash(c)}"
    # a key resource in terraform would land its private key in state and make a key on merge
    key_tf = 'resource "google_service_account_key" "k" {\n  service_account_id = x\n}\n'
    for payload in ({"tool_name": "Write", "tool_input": {"file_path": "/tmp/tf/key.tf", "content": key_tf}},
                    {"tool_name": "Edit", "tool_input": {"file_path": "/tmp/tf/key.tf", "old_string": "a",
                                                          "new_string": key_tf}},
                    {"tool_name": "apply_patch", "tool_input": {"command": "*** Begin Patch\n*** Add File: key.tf\n+"
                                                                + key_tf.replace("\n", "\n+") + "\n*** End Patch"}},
                    {"tool_name": "Write", "tool_input": {"file_path": "/tmp/tf/key.tf.json", "content":
                        '{"resource": {"google_service_account_key": {"k": {"service_account_id": "x"}}}}'}}):
        reason = tool_problem({**payload, "transcript_path": unlocked, "cwd": "/tmp"}, {"MUNICE_GCP_ADMIN": "1"})
        assert reason and "terraform#1040" in reason, f"key resource should be denied: {payload['tool_name']}"
    assert tool_problem({"tool_name": "Write", "tool_input": {"file_path": "/tmp/notes.md", "content":
        "we never use google_service_account_key"}, "transcript_path": unlocked, "cwd": "/tmp"}, {}) is None
    # a patch that removes a key resource is cleanup, not a new key (Codex review)
    assert tool_problem({"tool_name": "apply_patch", "tool_input": {"command": "*** Begin Patch\n*** Update File: key.tf\n"
        "@@\n-" + key_tf.rstrip("\n").replace("\n", "\n-") + "\n*** End Patch"}, "transcript_path": unlocked,
        "cwd": "/tmp"}, {}) is None
    slack_selftest(claude_transcript, tmp)
    print("selftest ok")


def slack_selftest(claude_transcript, tmp):
    """The Slack ✅ route. A fake approvals() stands in for slack_approval.py, which has its own tests."""
    calls, consumed = [], []

    def approvals(found=True, raises=False):
        def check(cmd):
            calls.append(cmd)
            if raises:
                raise RuntimeError("slack down")
            if not found:
                return None, "요유의 ✅가 없음"
            return (lambda: (cmd not in consumed, consumed.append(cmd))[0]), ""
        return check

    def run(cmd, *, env=None, found=True, raises=False, main=True, rider=None, tool="Bash", prompt="고쳐줘"):
        calls.clear()
        payload = {"tool_name": tool, "tool_input": {"command": cmd}, "transcript_path": claude_transcript(prompt), "cwd": "/tmp"}
        return hook(payload, env or {}, lambda _: rider, approvals(found, raises), lambda _: main)

    cmd = "gcloud --configuration=munice-admin firestore documents list --project miraclenight-f06c6"
    assert run(cmd) is None and calls == [cmd], "the main pane runs an approved command"
    assert "방금" in (run(cmd) or ""), "a second run of the same approval"
    consumed.clear()
    # pane-fit-guard asks the main pane once a message; the approval must survive that refusal for the repeat
    assert run(cmd, rider="pane-fit-guard: 칸으로?") == "pane-fit-guard: 칸으로?" and not consumed
    assert run(cmd) is None and consumed == [cmd]
    consumed.clear()
    reason = run(cmd, found=False)
    assert reason and "요유의 ✅가 없음" in reason and "prod 허용" in reason, "no approval: the typed unlock is still offered"
    assert "Slack을 읽지 못함" in (run(cmd, raises=True) or ""), "a Slack failure refuses"
    # never asked: another pane, an auto tab, or a refusal a ✅ does not lift
    for kw in ({"main": False}, {"env": {"MUNICE_AMBIENT": "1"}}, {"env": {"MUNICE_AUTO_DISPATCH": "1"}}, {"tool": "Monitor"}):
        assert run(cmd, **kw) and not calls, f"Slack must not be asked: {kw}"
    for c in ("MUNICE_GCP_ADMIN=1 direnv exec . gcloud run services update api",
              "CLOUDSDK_ACTIVE_CONFIG_NAME=munice-admin claude -p 'prod 고쳐'",
              "CLOUDSDK_ACTIVE_CONFIG_NAME=munice-admin codex exec 'x'",
              "gcloud --configuration=munice-admin auth print-access-token",
              "gcloud auth login", "gcloud config configurations activate munice-admin",
              "gcloud --configuration=munice-admin iam service-accounts keys create k.json --iam-account=a@x",
              "rm ~/.local/state/gcp-account-guard/slack-approvals/1793850000.000001"):
        assert run(c) and not calls, f"Slack must not lift: {c}"
    # a command the guard allows anyway never reaches Slack
    assert run("gcloud run services list --project miraclenight-f06c6") is None and not calls
    # a typed unlock comes first: no Slack read
    assert run(cmd, prompt="prod 허용") is None and not calls
    # the used marks are out of reach of the file tools too
    for tool, key in (("Write", "file_path"), ("Edit", "file_path"), ("Read", "file_path")):
        assert tool_problem({"tool_name": tool, "tool_input": {key: os.path.expanduser(
            "~/.local/state/gcp-account-guard/slack-approvals/1793850000.000001")}, "cwd": "/tmp"}, {"MUNICE_GCP_ADMIN": "1"})
    # the real loader: no module, a module that throws, and one that hangs past the deadline all refuse
    assert slack_approval(cmd, path=os.path.join(tmp, "none.py"))[0] is None
    for body, word in (("def find(c, n):\n    raise ValueError('x')\n", "ValueError"),
                       ("import time\ndef find(c, n):\n    time.sleep(5)\n", "초 안에")):
        path = os.path.join(tmp, f"m{len(os.listdir(tmp))}.py")
        open(path, "w").write(body)
        start = time.monotonic()
        found, why = slack_approval(cmd, deadline=0.3, path=path)
        assert found is None and word in why and time.monotonic() - start < 2, (why, time.monotonic() - start)
    path = os.path.join(tmp, "ok.py")
    open(path, "w").write("def find(c, n):\n    return '1793850000.000001', ''\n"
                          "def consume(ts, c):\n    return ts == '1793850000.000001'\n")
    found, why = slack_approval(cmd, path=path)
    assert found and found() is True and why == ""
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0), "no alarm left running"


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
        sys.exit()
    hook_input = json.load(sys.stdin)
    reason = hook(hook_input, dict(os.environ), riders, slack_approval, main_pane)
    if reason:  # JSON deny: Claude Code and Codex both read it
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }}, ensure_ascii=False))
