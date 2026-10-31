#!/usr/bin/env python3
"""Reads a user prompt hook payload and prints a reminder when 요유's message reads like a correction of how 유요 worked.

요유 2026-10-31: a correction means the whole kind, not the one case. Sessions kept fixing one case or adding a memory line.
Called by pane-label-nudge.sh, which both Claude and Codex already run on every message, so open sessions get it at once and
Codex needs no new hook trust. Only behavior corrections match, not "쉽게 설명해 줘": 1.3% of 4,252 prompts, about 4 in 5 real.
A miss costs nothing and a false hit costs one line, so the text leaves the call to the session. Fails open.

    correction-nudge.py --selftest
"""
from __future__ import annotations  # a hook whose python3 is macOS's 3.9 would crash and let everything through

import json
import re
import sys
import unicodedata

CORRECTION = re.compile(
    r"라니까|했잖아|랬잖|말했잖|말했는데|얘기했잖|하라고 했|하지 ?말라(?:고|니까)|하지 ?말랬"
    r"|왜 ?(?:또|자꾸|계속)|왜 ?안 ?(?:했|하|써|달|붙|열|닫|읽|봐|물|적|넣|해)|왜 내가|내가 왜|왜 나한테|나한테 왜"
    r"|안 ?했(?:어|냐|네)\?|말 ?좀 ?들|말귀|그게 아니라|아니라고|똑바로|제대로 ?(?:안|좀|해|읽|확인)"
    r"|몇 ?번(?:을|이나|째)? ?(?:말|똑같|같은)"
)
MACHINE = ("[pane-tell:", "[handoff:", "[요유 지시", "<hook_prompt", "<task-notification", "<system-reminder")
HEAD = 600  # the correction sits at the start; a long paste below it quotes other people's words
REMINDER = ("요유 메시지가 일하는 방식에 대한 지적으로 보입니다. 지적이면 그 건만 고치고 끝내지 말고 전역 규칙 `A correction means prevent "
            "recurrence`대로 같은 종류 찾기, 왜 안 막혔는지 찾기, 그 원인 고치기(메모 말고 훅·기본값·테스트), 새 세션과 열린 세션에서 "
            "확인까지 하세요. 사과하고 다시 쓰는 것은 해결이 아닙니다. 지적이 아니라 질문이면 답만 하세요.")


def reminder(text: str) -> str | None:
    text = unicodedata.normalize("NFC", text or "").strip()
    if text.startswith(MACHINE):
        return None
    return REMINDER if CORRECTION.search(text[:HEAD]) else None


def selftest():
    hit = ["왜 또 그렇게 했어", "왜자꾸 영어로 써", "하지 말라니까", "내가 말했잖아", "그게 아니라 다른 말이야", "왜 안 했어?",
           "말좀 들어라", "내가 왜 해야 해", "칸으로 하라고 했는데", "몇번 말해", "똑바로 해", "왜 안 닫아"]
    miss = ["로그 좀 봐 줘", "왜 안되는지 알아봐 줘", "쉽게 설명해 줘", "이 함수 뭐야?", "배포해 줘", "", "ㅇㅇ", "왜 이렇게 느리지"]
    for t in hit:
        assert reminder(t) == REMINDER, t
        assert reminder(unicodedata.normalize("NFD", t)) == REMINDER, f"NFD paste: {t}"
    for t in miss:
        assert reminder(t) is None, t
    assert reminder("[pane-tell: 다른 세션] 왜 또 그랬어") is None, "another session's words are not 요유's"
    assert reminder("로그입니다.\n" + "가" * HEAD + "\n왜 또 그랬어") is None, "a long paste is not a correction"
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
        sys.exit()
    try:
        message = reminder(json.load(sys.stdin).get("prompt") or "")
    except Exception:  # never break a prompt
        sys.exit(0)
    if message:
        print(message)
