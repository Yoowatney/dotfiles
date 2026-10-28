#!/usr/bin/env python3
"""Stop hook (Claude Code and Codex): a report that leaves something in `요유 몫:` is asked once whether 유요 could do it.

The global rule ends every finished report with `남은 것: 요유 몫: … · 유요 몫: … · 세션: …`. 요유 said "너가 해" /
"알아서 해" 9 times in 6 sessions on 10/22-10/23: each new skill or report slot brought the old "shall I?" step back,
filed under 요유 몫. This asks once, before the turn ends, whether every item there is truly 요유's.
Only the `요유 몫:` value counts, up to `·` or the end of its line; `없음` passes, and so does a reply without the line.
Code, quotes and blockquotes are ignored (a report about the format is not a report). Skipped for headless Claude runs
(claude -p / SDK) and on the second stop of a turn (stop_hook_active), so it blocks at most once. Fails open.

    yoyoo-share-guard.py --selftest
"""
import json
import os
import re
import sys
import unicodedata

STRIP = [
    re.compile(r"(```|~~~)[\s\S]*?(?:\1|\Z)"),  # fenced code, also unclosed
    re.compile(r"`[^`\n]*`"),  # inline code
    re.compile(r"^[ \t]*>[^\n]*", re.M),  # blockquote lines
    re.compile(r'"[^"\n]*"|“[^”\n]*”|‘[^’\n]*’|「[^」\n]*」'),  # quoted text
]
SHARE = re.compile(r"요유[ \t]*몫[ \t]*\**[ \t]*[:：][ \t]*\**([^·\n]*)")  # horizontal space only: never the next line
NONE = re.compile(r"^(?:없음|없습니다|none)\b", re.I)
REASON = ("yoyoo-share-guard: 보고의 '요유 몫'에 항목이 남아 있습니다. 이 가운데 유요가 지금 할 수 있는 것(명령 실행, "
          "Slack 글, 팀원과 이미 합의한 단계)은 지금 하고, 요유 몫에는 결정, 요유 화면에서만 할 수 있는 확인, 처음 정하는 "
          "prod 배포·데이터·과금만 남겼는가? 이미 그렇다면 보고를 그대로 두고 끝내세요.")


def final_text(path: str) -> str:
    """Text blocks of the last assistant message in a Claude transcript (blocks of one message share message.id)."""
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


def leaves_yoyoo_work(text: str) -> bool:
    text = unicodedata.normalize("NFC", text)
    for pat in STRIP:
        text = pat.sub(" ", text)
    for m in SHARE.finditer(text):
        value = m.group(1).strip(" \t*_.,;")
        if value and not NONE.match(value):
            return True
    return False


def decide(payload: dict, env) -> str | None:
    if payload.get("stop_hook_active") or env.get("CLAUDE_CODE_ENTRYPOINT", "").startswith("sdk"):
        return None
    text = payload.get("last_assistant_message")
    if text is None and payload.get("transcript_path"):
        text = final_text(payload["transcript_path"])  # unreadable transcript raises: fail open
    if not isinstance(text, str) or not leaves_yoyoo_work(text):
        return None
    return REASON


def selftest():
    import subprocess
    import tempfile

    cli = {"CLAUDE_CODE_ENTRYPOINT": "cli"}

    def p(text, **kw):
        return {"hook_event_name": "Stop", "stop_hook_active": False, "last_assistant_message": text, **kw}

    block = [
        "배포했습니다.\n\n남은 것: 요유 몫: PR 머지 · 유요 몫: 없음 · 세션: 닫아도 됨",
        "남은 것: 요유 몫: Slack에 공지 올리기 · 유요 몫: watch 대기 · 세션: 닫아도 됨",
        "남은 것:\n- 요유 몫: staging QA 확인\n- 유요 몫: 없음\n- 세션: 닫아도 됨",
        "**요유 몫:** 배포 승인",
        "요유 몫: 없음 · 유요 몫: 없음\n\n남은 것: 요유 몫: 결정 하나 · 유요 몫: 없음",  # the last line counts too
        "요유 몫：태그 확인",  # full-width colon
        unicodedata.normalize("NFD", "남은 것: 요유 몫: 머지 · 유요 몫: 없음"),  # NFD text from a macOS paste
    ]
    allow = [
        "남은 것: 요유 몫: 없음 · 유요 몫: watch 대기 · 세션: 닫아도 됨",
        "남은 것: 요유 몫: 없음 · 유요 몫: 없음 · 세션: 닫아도 됨",
        "남은 것:\n- 요유 몫: 없음\n- 유요 몫: PR CI 대기\n- 세션: 닫으면 Monitor 멈춤",
        "**요유 몫:** 없음",
        "요유 몫: 없음.",
        "요유 몫: 없습니다",
        "배포를 끝냈습니다. 테스트도 통과했습니다.",
        "요유 몫은 결정만 남기는 칸입니다.",  # no colon: talking about the slot
        "형식은 `남은 것: 요유 몫: … · 유요 몫: …` 입니다.",
        "```\n남은 것: 요유 몫: PR 머지\n```\n예시입니다.",
        "> 요유 몫: PR 머지\n이전 보고를 인용했습니다.",
        '"요유 몫: PR 머지"처럼 쓰면 훅이 되묻습니다.',
        "요유 몫:",  # empty value
        "남은 것: 요유 몫: · 유요 몫: 없음",  # empty value: the next field is not read as 요유's
        "남은 것:\n- 요유 몫:\n- 유요 몫: CI 대기",  # nor the next line (Codex review)
        "",
    ]
    for t in block:
        assert decide(p(t), cli), f"should block: {t[:60]!r}"
    for t in allow:
        assert decide(p(t), cli) is None, f"should allow: {t[:60]!r}"
    assert decide(p(block[0], stop_hook_active=True), cli) is None, "second stop of a turn must pass"
    assert decide(p(block[0]), {"CLAUDE_CODE_ENTRYPOINT": "sdk-cli"}) is None, "claude -p must pass"
    assert decide(p(block[0]), {}) == REASON, "Codex (no Claude entrypoint) must block"
    assert decide(p(None), cli) is None, "Codex null message without transcript must pass"

    def transcript(entries):
        f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        f.write("\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n")
        f.close()
        return f.name

    def said(mid, text):
        return {"type": "assistant", "message": {"id": mid, "role": "assistant", "content": [{"type": "text", "text": text}]}}

    prompt = {"type": "user", "message": {"role": "user", "content": "배포해 줘"}}
    with_share = transcript([prompt, said("m1", "읽겠습니다."), said("m2", "끝났습니다."), said("m2", block[0])])
    old_share = transcript([said("m0", block[0]), prompt, said("m2", allow[0])])
    try:
        assert decide(p(None, transcript_path=with_share), cli) == REASON, "transcript fallback must block"
        assert decide(p(None, transcript_path=old_share), cli) is None, "only the final message counts"
    finally:
        os.unlink(with_share)
        os.unlink(old_share)

    def run(stdin, env=cli):
        r = subprocess.run([sys.executable, "-I", os.path.realpath(__file__)], input=stdin,
                           capture_output=True, text=True, env={**os.environ, **env}, timeout=10)
        return r.returncode, r.stdout

    assert run("not json{") == (0, ""), "malformed input must exit 0 silently"
    assert run(json.dumps(p(None, transcript_path="/nonexistent/x.jsonl"))) == (0, ""), "missing transcript must exit 0"
    assert run(json.dumps(p(allow[0]))) == (0, ""), "요유 몫: 없음 must print nothing"
    assert run(json.dumps(p(block[0], stop_hook_active=True))) == (0, ""), "second stop must print nothing"
    code, out = run(json.dumps(p(block[0])))
    # Codex's stop.command.output schema has additionalProperties: false, so exactly these two keys.
    assert code == 0 and json.loads(out) == {"decision": "block", "reason": REASON}, out
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
