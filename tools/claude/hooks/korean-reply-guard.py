#!/usr/bin/env python3
"""Stop hook: send a final reply whose prose is mostly English back to be rewritten in Korean.

The global rule says "always respond in Korean", yet final replies keep coming out in
English. Code, paths, URLs and quotes are stripped before counting, so a Korean reply full
of identifiers passes. Skipped for non-interactive runs (claude -p / SDK), whose output
scripts parse, and on the second stop of a turn (stop_hook_active), so it rewrites at most once.

    korean-reply-guard.py --selftest
"""
from __future__ import annotations  # a hook whose python3 is macOS's 3.9 would crash and let everything through

import json
import os
import re
import sys


LATIN_MIN = 20  # below is "Done."/"OK"; local transcripts had English stop replies of 22-38 letters, no Korean hits
HANGUL_RATIO_MAX = 0.2  # Hangul/(Hangul+Latin): local English replies measured 0.00-0.11, Korean 0.34 and up

# Removed before counting. ASCII-only classes on purpose: \w and \S match Hangul and would eat Korean text.
STRIP = [
    re.compile(r"(```|~~~)[\s\S]*?(?:\1|\Z)"),  # fenced code, also unclosed
    re.compile(r"`[^`\n]*`"),  # inline code
    re.compile(r"^[ \t]*>[^\n]*", re.M),  # blockquote lines
    re.compile(r"^[ \t]*\|[^\n]*", re.M),  # table rows: data values, not prose
    re.compile(r"\]\([^)\s]*\)"),  # markdown link targets
    re.compile(r"\b[A-Za-z][A-Za-z0-9+.-]*://[!-~]+", re.A),  # URLs
    re.compile(r"(?:~|\.{1,2})?/[A-Za-z0-9_.~@%+-][A-Za-z0-9_.~/@%+-]*", re.A),  # absolute/home paths
    re.compile(r"\b[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+", re.A),  # relative paths
    re.compile(r"\b[A-Za-z0-9_-]+\.[A-Za-z0-9]{1,5}\b", re.A),  # bare file names: settings.json
    re.compile(r"\b(?=[a-f]*[0-9])[0-9a-f]{7,40}\b", re.A),  # commit SHAs
]
HANGUL = re.compile(r"[가-힣]")
LATIN = re.compile(r"[A-Za-z]")
REASON = ("korean-reply-guard: 방금 응답의 본문이 영어였습니다. 같은 내용을 한국어로 다시 쓰되 코드·경로·명령어는 그대로 두고, "
          "사용자가 영어 문장을 직접 요청했다면 그 부분만 코드 블록에 넣고, Codex 리뷰처럼 원문 그대로 옮겨야 하는 글은 "
          "번역하지 말고 `>` 인용으로 남기세요.")


def final_text_from_transcript(path: str) -> str:
    """Text blocks of the last assistant message. The transcript has one block per line;
    blocks of one message share message.id. A user line after it means it is not written yet."""
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    msg_id, texts = None, []
    for raw in reversed(lines):
        try:
            e = json.loads(raw)
        except ValueError:
            continue
        if e.get("type") == "user":
            break
        if e.get("type") != "assistant" or e.get("isSidechain"):
            continue
        m = e.get("message") or {}
        if msg_id is None:
            msg_id = m.get("id", "")
        elif m.get("id", "") != msg_id:
            break
        texts[:0] = [b.get("text", "") for b in m.get("content") or [] if isinstance(b, dict) and b.get("type") == "text"]
    return "\n".join(texts)


def decide(payload: dict, env) -> str | None:
    if payload.get("stop_hook_active") or env.get("CLAUDE_CODE_ENTRYPOINT", "").startswith("sdk"):
        return None  # second stop of this turn, or claude -p / SDK (entrypoint sdk-cli, sdk-ts, sdk-py)
    text = payload.get("last_assistant_message")
    if text is None:
        text = final_text_from_transcript(payload["transcript_path"])
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        json.loads(text)
        return None  # machine output
    except ValueError:
        pass
    for pat in STRIP:
        text = pat.sub(" ", text)
    hangul, latin = len(HANGUL.findall(text)), len(LATIN.findall(text))
    if latin >= LATIN_MIN and hangul / (hangul + latin) < HANGUL_RATIO_MAX:
        return REASON
    return None


def selftest():
    import subprocess
    import tempfile

    cli = {"CLAUDE_CODE_ENTRYPOINT": "cli"}

    def p(text, **kw):
        return {"hook_event_name": "Stop", "stop_hook_active": False, "last_assistant_message": text, **kw}

    block = [
        "I added the hook and wired it into settings. All tests pass now.\n\n"
        "Next I will run it against a real transcript and report the timing.",
        "I updated the guard and ran the suite again. 참고로 everything passes and the branch is ready for review.",
        "Your shareable insights report is ready:\nfile:///Users/x/report.html\n\n"
        "Want to dig into any section or try one of the suggestions?",
        "Now I'll record the staging decision in memory so it isn't proposed again.",
        "Waiting for CI to finish now.",
    ]
    allow = [
        "`no-sed-inplace.py`를 `~/.claude/hooks/`에 연결했습니다. PR #123 CI 통과.",
        "Cloud Run 인스턴스를 Terraform으로 줄였고 GitHub Actions에서 plan이 통과했습니다. "
        "settings.json, init.sh, korean-reply-guard.py 세 파일을 고쳤고 커밋은 a1b2c3d4e5f6입니다.",
        "읽기/쓰기 권한을 나눴고 staging/prod 둘 다 https://example.com에서 확인했습니다. 남은 것: 없음",
        "훅을 고쳤습니다. 바뀐 코드는 아래와 같습니다.\n\n```python\n"
        + "def handle(payload):\n    return payload.get('message') or 'nothing to see here at all'\n" * 8
        + "```\n\n테스트도 모두 통과했습니다.",
        "Codex 리뷰 결과를 그대로 옮깁니다.\n\n> The parser drops the last line when the input has no trailing newline,\n"
        "> and the retry loop never backs off. Consider adding jitter to the retry delay as well.\n\n"
        "두 지적 모두 맞아서 반영했습니다.",
        "Done.",
        "OK",
        "Merged, CI green.",
        "CI가 통과했습니다. Codex review가 끝나면 merge하겠습니다.",
        "Code-reviewer 리뷰와 SuperDocs Codex 리뷰를 기다리고 있습니다.",
        "불일치 항목 전체입니다.\n\n| id | store | status | plan |\n|---|---|---|---|\n"
        + "| abcDEF | play_store | expired | monthly |\n" * 15,
        '{"status": "ok", "items": [{"name": "alpha", "description": "first english item with long text"}]}',
    ]
    for t in block:
        assert decide(p(t), cli), f"should block: {t[:60]!r}"
    for t in allow:
        assert decide(p(t), cli) is None, f"should allow: {t[:60]!r}"
    assert decide(p(block[0], stop_hook_active=True), cli) is None, "second stop of a turn must pass"
    assert decide(p(block[0]), {"CLAUDE_CODE_ENTRYPOINT": "sdk-cli"}) is None, "claude -p must pass"
    assert decide(p(block[0]), {"CLAUDE_CODE_ENTRYPOINT": "sdk-ts"}) is None, "SDK must pass"

    def run(stdin):
        r = subprocess.run([sys.executable, "-I", os.path.realpath(__file__)], input=stdin,
                           capture_output=True, text=True, env={**os.environ, **cli}, timeout=10)
        return r.returncode, r.stdout

    assert run("not json{") == (0, ""), "malformed input must exit 0 silently"
    assert run(json.dumps({"stop_hook_active": False, "transcript_path": "/nonexistent/x.jsonl"})) == (0, ""), \
        "missing transcript must exit 0 silently"
    code, out = run(json.dumps(p(block[0])))
    assert code == 0 and json.loads(out)["decision"] == "block", f"block output: {out!r}"

    # Transcript fallback, real JSONL shape: one content block per line, blocks of one message share message.id.
    def line(kind, mid=None, block=None, text=""):
        if kind == "assistant":
            return {"type": "assistant", "isSidechain": False, "uuid": "u-" + mid,
                    "message": {"id": mid, "type": "message", "role": "assistant", "stop_reason": "end_turn",
                                "content": [{"type": block, block: text} if block != "tool_use"
                                            else {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}]}}
        if kind == "user":
            return {"type": "user", "isSidechain": False, "message": {"role": "user", "content": text}}
        return {"type": kind}

    def transcript(entries):
        f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        f.write("\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n")
        f.close()
        return f.name

    english_final = transcript([
        line("user", text="훅 상태 확인해 줘"),
        line("assistant", "msg_a", "text", "먼저 설정 파일을 읽겠습니다."),
        line("assistant", "msg_a", "tool_use"),
        line("user", text=[{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]),
        line("assistant", "msg_b", "thinking", ""),
        line("assistant", "msg_b", "text", "The hook is registered and the settings file is valid JSON."),
        line("attachment"),
        line("assistant", "msg_b", "text", "Nothing else changed, and the other stop hooks still run as before."),
        line("api-request"),
    ])
    korean_final = transcript([
        line("user", text="상태 알려 줘"),
        line("assistant", "msg_a", "text", "I will read the settings file first and then check every hook entry."),
        line("assistant", "msg_a", "tool_use"),
        line("user", text=[{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]),
        line("assistant", "msg_b", "text", "훅이 등록되어 있고 설정 파일도 올바릅니다. 다른 Stop 훅은 그대로입니다."),
    ])
    try:
        no_msg = {"hook_event_name": "Stop", "stop_hook_active": False}
        assert decide({**no_msg, "transcript_path": english_final}, cli), "English final message in transcript"
        assert decide({**no_msg, "transcript_path": korean_final}, cli) is None, "only the final message counts"
        code, out = run(json.dumps({**no_msg, "transcript_path": english_final}))
        assert code == 0 and json.loads(out)["decision"] == "block", f"transcript block output: {out!r}"
    finally:
        os.unlink(english_final)
        os.unlink(korean_final)
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
        sys.exit()
    try:
        reason = decide(json.load(sys.stdin), os.environ)
    except Exception:  # never break a session
        sys.exit(0)
    if reason:
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
