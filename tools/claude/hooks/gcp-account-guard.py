#!/usr/bin/env python3
"""PreToolUse guard (Claude Code and Codex): keep AI sessions on the agent GCP account.

AI sessions run as an agent service account (production read, staging write) through the
default `munice` gcloud config and ADC files. Yoyoo's own account lives in the `munice-admin`
config and `adc-munice-admin.json`. This hook refuses commands that reach that identity or
change which identity gcloud/firebase use, unless the session chose admin (`cla` sets
MUNICE_GCP_ADMIN=1) or Yoyoo's latest typed prompt contains `prod 허용` (that turn only).
terraform#1005.

    gcp-account-guard.py --selftest
"""
import json
import os
import re
import sys

UNLOCK = "prod 허용"

# ponytail: text patterns over the whole command, heredoc bodies included (a script there runs).
# They stop accidents (a wrong config, a script that grabs the admin ADC), not a session set on
# getting around them: a script can still read the credential store without naming it here.
SWITCH = [
    # Picking the config, not mentioning it: notes and commit messages may name it.
    (re.compile(r"(?:--configuration[= ]+|CLOUDSDK_ACTIVE_CONFIG_NAME=|\bactivate\s+)['\"]?munice-admin"),
     "요유 계정 구성(munice-admin)"),
    # .envrc picks the admin config when this is set, e.g. under `direnv exec`.
    (re.compile(r"\bMUNICE_GCP_ADMIN="), "요유 계정 세션 표시(MUNICE_GCP_ADMIN)"),
    # Setting it in a config is the `config set` line below; `config get` only reads.
    (re.compile(r"--impersonate-service-account\b"), "다른 서비스 계정 대리 실행"),
    (re.compile(r"\bconfig\s+(?:set|unset)\s+(?:core/)?(?:account|auth/\S+)"), "gcloud 계정 설정 변경"),
    (re.compile(r"\bauth\s+(?:login|activate-service-account|application-default\s+login)\b"), "gcloud 로그인"),
    (re.compile(r"\bfirebase\s+login(?!:list\b)"), "firebase 로그인"),  # login:use, login:ci, login:add too
    (re.compile(r"\bCLOUDSDK_(?:CONFIG|CORE_ACCOUNT|AUTH_[A-Z_]+)="), "gcloud 신원 환경 변수"),
    (re.compile(r"--account[= ]\S+@"), "gcloud --account"),
    (re.compile(r"credentials\.db|access_tokens\.db|legacy_credentials|application_default_credentials\.json"
                r"|adc-munice|\.config/gcloud/configurations|configstore/firebase-tools\.json"), "자격 증명 파일"),
]
# Pointing ADC at another file swaps the identity of every library call; gomgom keeps its own file.
ADC_ASSIGN = re.compile(r"\bGOOGLE_APPLICATION_CREDENTIALS=(\S*)")
CONFIG_DIRS = re.compile(r"/\.config/gcloud/|configstore/firebase-tools\.json")
WRITERS = {"Write", "Edit", "MultiEdit", "NotebookEdit", "apply_patch"}


def bash_problem(cmd: str) -> str | None:
    for pattern, what in SWITCH:
        if pattern.search(cmd):
            return what
    for m in ADC_ASSIGN.finditer(cmd):
        if "gomgom" not in m.group(1):
            return "GOOGLE_APPLICATION_CREDENTIALS 바꾸기"
    return None


PATCH_PATHS = re.compile(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+)$", re.M)


def write_targets(tool: str, inp: dict) -> str:
    """The paths a write lands on; the content may quote config paths freely."""
    if tool == "apply_patch":
        return "\n".join(PATCH_PATHS.findall(str(inp.get("command") or inp.get("input") or inp.get("patch") or "")))
    return str(inp.get("file_path") or inp.get("notebook_path") or "")


def write_problem(text: str) -> str | None:
    return "gcloud·firebase 설정 파일 쓰기" if CONFIG_DIRS.search(text) else None


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
            if not text.lstrip().startswith(("# AGENTS.md", "<environment_context>", "<user_instructions>")):
                last = text
    return last


def tool_problem(payload: dict, env: dict) -> str | None:
    tool, inp = payload.get("tool_name"), payload.get("tool_input") or {}
    if tool in ("Bash", "Monitor"):
        what = bash_problem(inp.get("command") or "")
    elif tool in WRITERS:
        what = write_problem(write_targets(tool, inp))
    else:
        return None
    if not what or env.get("MUNICE_GCP_ADMIN") == "1":
        return None
    # Job-opened tabs get their prompts typed in by a job (ambient pastes Slack threads), so the word proves nothing.
    auto = env.get("MUNICE_AUTO_DISPATCH") == "1" or env.get("MUNICE_AMBIENT") == "1"
    if not auto and UNLOCK in latest_prompt(payload.get("transcript_path") or ""):
        return None
    if auto:
        return (f"{what}: 자동 탭은 에이전트 계정(staging 쓰기, prod 읽기)으로만 GCP를 쓰고, 이 잠금은 풀 수 없습니다. "
                "prod 쓰기가 필요하면 무엇을 왜 바꾸는지 보드 항목에 적고 owner를 yoyoo로 넘길 것. terraform#1005")
    return (f"{what}: AI 세션은 에이전트 계정(staging 쓰기, prod 읽기)으로만 GCP를 씁니다. prod 쓰기가 꼭 필요하면 "
            f"여기서 멈추고, 무엇을 왜 바꾸는지 적은 뒤 요유에게 '`{UNLOCK}`이라고 입력하면 이번 턴만 요유 계정으로 "
            "할 수 있다'고 말할 것. 처음부터 prod 작업 세션이면 요유가 `cla`로 띄움. terraform#1005")


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
    ]
    allow = [
        "gcloud run services list --project example-prod",
        "gcloud logging read 'severity>=ERROR' --project=example-prod --limit 5",
        "bq query --nouse_legacy_sql 'SELECT 1'",
        "gcloud config configurations activate gomgom",
        "GOOGLE_APPLICATION_CREDENTIALS=$HOME/.config/gcloud/adc-gomgom.json node gomgom.js",
        "gcloud config list",
        "gcloud config get auth/impersonate_service_account --configuration=munice",
        "gcloud auth list",
        "firebase projects:list",
        "firebase login:list",
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
    # subagent transcript has no human line
    assert tool_problem({**admin_cmd, "transcript_path": claude_transcript(tail_tool=True)}, {})
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
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
        sys.exit()
    reason = tool_problem(json.load(sys.stdin), dict(os.environ))
    if reason:  # JSON deny: Claude Code and Codex both read it
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"gcp-account-guard: {reason}",
        }}, ensure_ascii=False))
