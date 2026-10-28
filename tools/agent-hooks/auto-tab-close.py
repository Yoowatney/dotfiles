#!/usr/bin/env python3
"""Stop hook (Claude Code and Codex): a tab a job opened closes itself when its report says nothing is left there.

The global rule asks a dispatch tab (MUNICE_AUTO_DISPATCH=1) or a Slack ambient tab (MUNICE_AMBIENT=1) to end with
`세션: 닫음` and `pane-label --close` as its last tool call. Sessions kept writing `세션: 닫아도 됨` and stopping, so 요유
closed the tabs by hand (요유 2026-10-28: "자동탭에서 할거없으면 좀 알아서 닫으라니까 죽어도 안닫네 ... 로직을
개선하라고"). This hook reads the report instead of trusting the session to remember: `세션: 닫음`, or `세션: 닫아도 됨`
with `요유 몫: 없음`, runs `pane-label --close`. That command keeps its own guards: only job tabs, never a 🔴 pane, and it
waits for the turn to end and cancels if the agent works again within 20 seconds (요유 typed, a goal judge sent it back).
A `요유 몫` with something in it keeps the tab open even at `닫아도 됨`: 요유 may need to type in it (`결정 필요`, `승격`).
Never blocks; prints nothing. Fails open.

    auto-tab-close.py --selftest
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
import unicodedata

sys.dont_write_bytecode = True  # no __pycache__ in the dotfiles at every stop
HERE = os.path.dirname(os.path.realpath(__file__))
_spec = importlib.util.spec_from_file_location("share", os.path.join(HERE, "yoyoo-share-guard.py"))
share = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(share)  # reuse its 요유 몫 reader, code and quote stripping, and transcript fallback

SESSION = re.compile(r"세션[ \t]*\**[ \t]*[:：][ \t]*\**([^·\n]*)")


def should_close(text: str) -> bool:
    """The last `세션:` value says 닫음, or 닫아도 됨 with nothing in 요유 몫. Code and quotes are not a report."""
    text = unicodedata.normalize("NFC", text)
    for pat in share.STRIP:
        text = pat.sub(" ", text)
    values = [m.group(1).strip(" \t*_.,;") for m in SESSION.finditer(text)]
    if not values:
        return False
    if values[-1].startswith("닫음"):
        return True
    return values[-1].startswith("닫아도 됨") and not share.leaves_yoyoo_work(text)


def decide(payload: dict, env) -> bool:
    if not (env.get("MUNICE_AUTO_DISPATCH") or env.get("MUNICE_AMBIENT")):
        return False
    text = payload.get("last_assistant_message")
    if text is None and payload.get("transcript_path"):
        text = share.final_text(payload["transcript_path"])  # unreadable transcript raises: fail open
    return isinstance(text, str) and should_close(text)


def selftest():
    import tempfile

    close = [
        "끝났습니다.\n\n남은 것: 요유 몫: 없음 · 유요 몫: 없음 · 세션: 닫아도 됨",  # this session, 2026-10-28
        "남은 것: 요유 몫: 없음 · 유요 몫: watch 대기 · 세션: 닫음",
        "남은 것: 요유 몫: GitHub에서 draft PR 머지 · 유요 몫: 없음 · 세션: 닫음",  # 요유's part is outside the tab
        "남은 것:\n- 요유 몫: 없음\n- 유요 몫: 없음\n- 세션: 닫아도 됨",
        "**남은 것:** 요유 몫: **없음** · 유요 몫: 없음 · 세션: **닫아도 됨**",
        unicodedata.normalize("NFD", "남은 것: 요유 몫: 없음 · 유요 몫: 없음 · 세션: 닫아도 됨"),
    ]
    keep = [
        "남은 것: 요유 몫: 이 탭에 \"반영\" 입력 · 유요 몫: 반영 후 닫기 · 세션: 닫아도 됨",  # 결정 필요, this session
        "남은 것: 요유 몫: 이 탭에서 승격 입력 · 유요 몫: prod 배포 · 세션: 닫으면 승격 못 함",
        "남은 것: 요유 몫: 없음 · 유요 몫: CI 대기 · 세션: 닫으면 Monitor 멈춤",
        "진행 중입니다. CI를 기다립니다.",
        "형식은 `세션: 닫음` 입니다.",  # talking about the format
        "> 남은 것: 요유 몫: 없음 · 세션: 닫음\n이전 보고를 인용했습니다.",
        "",
    ]
    for t in close:
        assert should_close(t), f"should close: {t[:70]!r}"
    for t in keep:
        assert not should_close(t), f"should keep: {t[:70]!r}"

    auto, done = {"MUNICE_AUTO_DISPATCH": "1"}, close[0]
    assert decide({"last_assistant_message": done}, auto)
    assert decide({"last_assistant_message": done}, {"MUNICE_AMBIENT": "1"})
    assert not decide({"last_assistant_message": done}, {}), "a session 요유 opened is never closed"
    assert not decide({"last_assistant_message": keep[0]}, auto)
    assert not decide({"last_assistant_message": None}, auto)

    sandbox = tempfile.mkdtemp()
    fake = os.path.join(sandbox, "pane-label")
    with open(fake, "w") as f:
        f.write(f'#!/bin/sh\necho "$@" >> {sandbox}/calls\n')
    os.chmod(fake, 0o755)

    def run(payload, env):
        r = subprocess.run([sys.executable, "-I", os.path.realpath(__file__)], input=payload, capture_output=True,
                           text=True, env={"PATH": os.environ.get("PATH", ""), "AUTO_TAB_CLOSE_PANE_LABEL": fake, **env},
                           timeout=10)
        calls = open(f"{sandbox}/calls").read() if os.path.exists(f"{sandbox}/calls") else ""
        if os.path.exists(f"{sandbox}/calls"):
            os.unlink(f"{sandbox}/calls")
        return r.returncode, r.stdout, calls

    assert run(json.dumps({"last_assistant_message": done}), auto) == (0, "", "--close\n"), "done report must close"
    assert run(json.dumps({"last_assistant_message": keep[0]}), auto) == (0, "", ""), "decision left must stay"
    assert run(json.dumps({"last_assistant_message": done}), {}) == (0, "", ""), "요유's own session must stay"
    assert run("not json{", auto) == (0, "", ""), "malformed input must exit 0 silently"
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
        sys.exit()
    try:
        if decide(json.load(sys.stdin), os.environ):
            pl = os.environ.get("AUTO_TAB_CLOSE_PANE_LABEL") or os.path.join(HERE, "pane-label")
            subprocess.run([pl, "--close"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=5)
    except Exception:  # never break a session
        pass
    sys.exit(0)
