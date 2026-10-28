#!/usr/bin/env python3
"""Stop hook (Claude Code and Codex): a report that leaves something in `요유 몫:` is asked once whether 유요 could do it.

The global rule ends every finished report with `남은 것: 요유 몫: … · 유요 몫: … · 세션: …`. 요유 said "너가 해" /
"알아서 해" 9 times in 6 sessions on 10/22-10/23: each new skill or report slot brought the old "shall I?" step back,
filed under 요유 몫. This asks once, before the turn ends, whether every item there is truly 요유's. In the same block it asks for the
missing `무엇을:` when a reply carries more `왜:` labels than `무엇을:` labels (the rule pairs them).
Only the `요유 몫:` value counts, up to `·` or the end of its line; `없음` passes, and so does a reply without the line.
Code, quotes and blockquotes are ignored (a report about the format is not a report). Skipped for headless Claude runs
(claude -p / SDK) and on the second stop of a turn (stop_hook_active), so it blocks at most once. Fails open.
Claude runs it with --claude: the question goes back as Stop additionalContext, which Claude shows as "Stop hook feedback"
instead of a red "Stop hook error". Codex keeps decision:block (its schema allows only decision and reason). The flag is
explicit because CLAUDE_CODE_ENTRYPOINT leaks into commands Claude runs, Codex included.

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
# Short, since Claude prints it under the report. The rewrite puts the report back at the bottom, so 요유 does not
# scroll up past a "그대로 둡니다" note to find it (요유 2026-10-28). Asked to end silently, Opus rewrote it anyway (2 of 2).
REASON = ("yoyoo-share-guard: 요유 몫 가운데 유요가 지금 할 수 있는 일(명령 실행, Slack 글, 합의된 단계)은 지금 하세요. "
          "그다음 설명을 덧붙이지 말고, 보고 전체를 다시 써서 끝내세요(바뀐 곳만 고쳐서).")
# 요유 2026-10-28 ("왜는 쓰는데 무엇을을 왜 안쓰지, 모든 세션이 다그러네"): every `왜:` label comes with a `무엇을:` label
WHY = re.compile(r"(?<![가-힣])왜[ \t]*\**[ \t]*[:：]")
WHAT = re.compile(r"무엇을[ \t]*\**[ \t]*[:：]")
WHAT_REASON = ("yoyoo-share-guard: 보고에 '왜:'는 있는데 짝이 되는 '무엇을:'이 없습니다. '왜:' 줄마다 바로 위에 "
               "'무엇을:' 줄(무엇이 바뀌었거나 바뀌는지, 파일·함수 이름 없이 쉬운 한국어)을 붙여 보고를 다시 쓰세요.")


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


def prose(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    for pat in STRIP:
        text = pat.sub(" ", text)
    return text


def misses_what(text: str) -> bool:
    text = prose(text)
    return len(WHY.findall(text)) > len(WHAT.findall(text))


def leaves_yoyoo_work(text: str) -> bool:
    text = prose(text)
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
    if not isinstance(text, str):
        return None
    reasons = [r for r, hit in ((WHAT_REASON, misses_what(text)), (REASON, leaves_yoyoo_work(text))) if hit]
    return " ".join(reasons) or None


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
    what_block = [
        "고쳤습니다.\n\n왜: 자동 탭이 막혀서\n남은 것: 요유 몫: 없음 · 유요 몫: 없음 · 세션: 닫아도 됨",
        "- **왜:** 배포가 멈춰서",
        "1. 무엇을: 정규식 변경\n   왜: 막혀서\n2. 왜: 테스트가 없어서",  # one 무엇을 short
        "왜：전각 콜론",
    ]
    what_allow = [
        "무엇을: 잠금 조회 고침\n왜: 자동 탭이 막혀서\n남은 것: 요유 몫: 없음 · 유요 몫: 없음",
        "**무엇을:** 정규식\n**왜:** 막혀서",
        "- 무엇을: a · 왜: b",
        "왜 그런지 설명합니다. 왜? 막혀서입니다.",
        "`왜:` 줄을 쓰세요.",
        '"왜:" 줄만 쓰면 훅이 되묻습니다.',
    ]
    for t in what_block:
        assert decide(p(t), cli) == WHAT_REASON, f"should ask for 무엇을: {t[:60]!r}"
    for t in what_allow:
        assert decide(p(t), cli) is None, f"should allow: {t[:60]!r}"
    both = "왜: 막혀서\n남은 것: 요유 몫: PR 머지 · 유요 몫: 없음"
    assert decide(p(both), cli) == f"{WHAT_REASON} {REASON}", "both checks in one block"

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

    def run(stdin, env=cli, args=()):
        r = subprocess.run([sys.executable, "-I", os.path.realpath(__file__), *args], input=stdin,
                           capture_output=True, text=True, env={**os.environ, **env}, timeout=10)
        return r.returncode, r.stdout

    assert run("not json{") == (0, ""), "malformed input must exit 0 silently"
    assert run("not json{", args=["--claude"]) == (0, ""), "malformed input must exit 0 silently (Claude)"
    assert run(json.dumps(p(None, transcript_path="/nonexistent/x.jsonl"))) == (0, ""), "missing transcript must exit 0"
    assert run(json.dumps(p(allow[0]))) == (0, ""), "요유 몫: 없음 must print nothing"
    assert run(json.dumps(p(allow[0])), args=["--claude"]) == (0, ""), "요유 몫: 없음 must print nothing (Claude)"
    assert run(json.dumps(p(block[0], stop_hook_active=True))) == (0, ""), "second stop must print nothing"
    code, out = run(json.dumps(p(block[0])))
    # Codex's stop.command.output schema has additionalProperties: false, so exactly these two keys.
    assert code == 0 and json.loads(out) == {"decision": "block", "reason": REASON}, out
    # Claude shows decision:block as a red "Stop hook error"; additionalContext continues the turn as yellow feedback.
    code, out = run(json.dumps(p(block[0])), args=["--claude"])
    assert code == 0 and json.loads(out) == {"hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": REASON}}, out
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
        sys.exit()
    try:
        reason = decide(json.load(sys.stdin), os.environ)
    except Exception:  # never break a session
        sys.exit(0)
    if reason and "--claude" in sys.argv:
        # Same once-per-turn continuation as a block (stop_hook_active is set), shown as feedback, not an error.
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": reason}}, ensure_ascii=False))
    elif reason:
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
