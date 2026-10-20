#!/usr/bin/env python3
"""UserPromptSubmit hook (Claude Code and Codex): refuse a prompt that carries a pasted secret.

Production ID tokens and API keys were pasted into chat prompts several times; once sent, they sit
in the Claude/Codex session logs. A block keeps the value from the model and from whatever the agent
would write next (files, memory, the work-log); it does not keep it out of the local session jsonl,
which still records the blocked prompt (claude -p 2.1.304, checked 2026-10-20). Block on tokens that are always secret; only warn on Google API
keys (AIza...), because Firebase web config keys are public while Gemini keys are not. Messages name
the kind of value, never the value. Both tools take the same output JSON (Codex's user-prompt-submit
output schema has decision:"block" + reason and hookSpecificOutput.additionalContext), so one shape.
Fails open: any error exits 0 with no output.

    secret-paste-guard.py --selftest
"""
import json
import os
import re
import sys

B64 = r"[A-Za-z0-9_-]"
# Patterns mirror secret-scan-push.sh; JWT and github tokens added. Minimum lengths keep prefixes in prose from matching.
BLOCK = [
    ("JWT(ID 토큰)", re.compile(rf"eyJ{B64}{{10,}}\.eyJ{B64}{{10,}}\.{B64}{{10,}}")),
    ("Anthropic API 키", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("Slack 토큰", re.compile(r"xox[abposr]-[A-Za-z0-9-]{10,}")),
    ("GitHub 토큰", re.compile(r"gh[po]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}")),
    ("개인 키(PRIVATE KEY)", re.compile(r"-----BEGIN ([A-Z]+ )*PRIVATE KEY( BLOCK)?-----")),
    ("서비스 계정 JSON", re.compile(r'"private_key_id"\s*:\s*"[0-9a-fA-F]{32,}"')),
]
WARN = [("Google API 키", re.compile(r"AIza[0-9A-Za-z_-]{35}"))]
REASON = ("secret-paste-guard: 프롬프트에 {kinds}로 보이는 값이 있어 모델에 보내지 않았습니다. "
          "값은 파일이나 환경변수에 두고 경로나 변수 이름만 알려 주세요. 이 기기의 세션 기록에는 방금 붙여 넣은 값이 남아 있습니다.")
NOTE = ("secret-paste-guard: 프롬프트에 {kinds}로 보이는 값이 있다. Firebase 웹 설정 키처럼 공개 값일 수 있지만 "
        "Gemini 등 비공개 키일 수도 있다. 값을 응답·파일·메모리·커밋에 다시 쓰지 말고, 필요하면 환경변수나 파일 경로로만 다룬다.")


def decide(payload: dict, env=os.environ) -> dict | None:
    # Headless runs (claude -p: the board watch job) build prompts from issue text, not from a human paste;
    # a token quoted in one issue must not silently stop the job.
    if env.get("CLAUDE_CODE_ENTRYPOINT", "").startswith("sdk"):
        return None
    prompt = payload.get("prompt")
    if not isinstance(prompt, str):
        return None
    blocked = [name for name, pat in BLOCK if pat.search(prompt)]
    if blocked:
        return {"decision": "block", "reason": REASON.format(kinds=", ".join(blocked))}
    warned = [name for name, pat in WARN if pat.search(prompt)]
    if warned:
        return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                       "additionalContext": NOTE.format(kinds=", ".join(warned))}}
    return None


def selftest():
    import subprocess

    os.environ["CLAUDE_CODE_ENTRYPOINT"] = "cli"  # the checks below are for a human session; subprocesses inherit it

    def fake(*parts):  # built at runtime so this file never holds a secret-looking literal
        return "".join(parts)

    jwt = fake("eyJ", "a" * 20, ".eyJ", "b" * 30, ".", "c" * 40)
    block = {
        "JWT": f"프로덕션 토큰이야 {jwt} 로 호출해 줘",
        "anthropic": fake("키는 sk-", "ant-", "api03-", "x" * 30),
        "slack": fake("xox", "b-", "1" * 12, "-", "a" * 12),
        "ghp": fake("gh", "p_", "A" * 36),
        "gho": fake("gh", "o_", "A" * 36),
        "github_pat": fake("github", "_pat_", "A" * 40),
        "private key": fake("-----BEGIN ", "PRIVATE KEY-----\nMIIE\n"),
        "rsa private key": fake("-----BEGIN RSA ", "PRIVATE KEY-----"),
        "service account": fake('{"private_key', '_id": "', "ab12" * 10, '"}'),
    }
    allow = {
        "short jwt prefix": "eyJhbGciOi",
        "jwt header only": "헤더는 eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9 로 시작해",
        "korean": "내일 배포 일정 확인하고 PR 올려 줘. 토큰은 ~/.config/foo/.env 에 있어.",
        "prefix mentions": "sk-ant- 키와 xoxb- 토큰, ghp_ 접두사는 쓰지 말자",
        "short key id": '"private_key_id" 필드가 비어 있는지 봐 줘',
        "empty": "",
    }
    for name, text in block.items():
        out = decide({"prompt": text})
        assert out and out["decision"] == "block" and "reason" in out, f"should block: {name}"
        # the reason must name a kind and never echo the pasted value
        assert text[-12:] not in out["reason"] and "sk-" not in out["reason"], f"reason leaks value: {name}"
    for name, text in allow.items():
        assert decide({"prompt": text}) is None, f"should allow: {name}"
    assert decide({"prompt": block["JWT"]}, {"CLAUDE_CODE_ENTRYPOINT": "sdk-cli"}) is None, "headless runs pass"
    gem = fake("AI", "za", "S" * 35)
    warn = decide({"prompt": f"Gemini 키 {gem} 쓰면 돼?"})
    assert warn and "decision" not in warn and warn["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit", warn
    assert gem not in json.dumps(warn), "warning must not echo the key"
    assert decide({"prompt": f"{gem} {jwt}"})["decision"] == "block", "block wins over warn"
    assert decide({"prompt": 5}) is None and decide({}) is None

    def run(stdin):
        r = subprocess.run([sys.executable, "-I", os.path.realpath(__file__)], input=stdin,
                           capture_output=True, text=True, timeout=10)
        return r.returncode, r.stdout

    claude = {"hook_event_name": "UserPromptSubmit", "session_id": "s", "cwd": "/tmp", "prompt": block["JWT"]}
    codex = {**claude, "turn_id": "t", "model": "gpt", "permission_mode": "default"}
    for payload in (claude, codex):
        code, out = run(json.dumps(payload))
        assert code == 0 and json.loads(out)["decision"] == "block", out
        assert jwt not in out
    assert run("not json{") == (0, ""), "malformed input must exit 0 silently"
    assert run(json.dumps({**claude, "prompt": allow["korean"]})) == (0, ""), "clean prompt prints nothing"
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
        sys.exit()
    try:
        out = decide(json.load(sys.stdin))
    except Exception:  # never break a session
        sys.exit(0)
    if out:
        print(json.dumps(out, ensure_ascii=False))
