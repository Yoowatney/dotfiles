#!/usr/bin/env python3
"""Stop hook (Claude Code and Codex): a report that leaves something in `요유 몫:` is asked once whether 유요 could do it.

The global rule ends every finished report with `남은 것: 요유 몫: … · 유요 몫: … · 세션: …`. 요유 said "너가 해" /
"알아서 해" 9 times in 6 sessions on 10/22-10/23: each new skill or report slot brought the old "shall I?" step back,
filed under 요유 몫. This asks once, before the turn ends, whether every item there is truly 요유's, and the same of a reply that
ends by asking leave with no such line (`머지할까요?`). In the same block it asks for the
missing `무엇을:` when a reply carries more `왜:` labels than `무엇을:` labels (the rule pairs them), and for the missing line
when `원인:`, `조치:` and `재발 방지:` do not come in equal numbers.
Only the `요유 몫:` value counts, up to `·` or the end of its line; `없음` passes, and so does a reply without the line.
Code, quotes and blockquotes are ignored (a report about the format is not a report). Skipped for headless Claude runs
(claude -p / SDK) and on the second stop of a turn (stop_hook_active), so it blocks at most once. Fails open.
A report whose 요유 몫 holds only decisions is not asked at all (only_decisions); auto-tab-close still sees it as work left.
It also sends back names 요유 cannot read: a pane's herdr id (`w5:pKR`, 27 reports in 14 days; the reason carries the
pane's name) and the machine names the global rule bans. A last report (it has `남은 것:`) also comes back while a test pane
this session opened (`임시 …`) is open: 4 of them stayed idle after this hook's own tests (10/31). A report after an explainer
page Claude had to write itself because Codex failed comes back unless it says so (요유 2026-10-31). In a Claude session a
long reply with no page comes back and goes to Codex (요유 2026-11-01); a Codex session writes its own.
A report with nothing left to do ends without a word: the question used to demand a rewrite. Of 102 fires on 10/28-10/31,
25 went on to a tool call and 69 only wrote text, most of it the same report again.
Claude runs it with --claude: the question goes back as Stop additionalContext, which Claude shows as "Stop hook feedback"
instead of a red "Stop hook error". Codex keeps decision:block (its schema allows only decision and reason). The flag is
explicit because CLAUDE_CODE_ENTRYPOINT leaks into commands Claude runs, Codex included.

    yoyoo-share-guard.py --selftest
"""
from __future__ import annotations  # a hook whose python3 is macOS's 3.9 would crash and let everything through

import importlib.util
import json
import os
import re
import subprocess
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
# Short, since Claude prints it under the report. A note such as "그대로 둡니다" would bury the report (요유 2026-10-28),
# and a rewrite prints it twice (요유 2026-10-31), so a turn with nothing to do ends without a word.
# The aside: on 10/29 a session answered this question by adding why only 요유 could do it, and 유요 had another way (api#975).
DO_REASON = ("yoyoo-share-guard: 요유 몫 가운데 유요가 지금 할 수 있는 일(명령 실행, Slack 글, 합의된 단계)은 지금 하세요. "
             "막혔다는 사유를 붙여 남기지 말고 다른 경로를 찾으세요. prod에 닿는 배포·머지, 데이터, 외부 설정, 과금은 요유의 확인이 있어야 하니 하지 말고 그대로 두세요.")
END_QUIETLY = ("그런 일이 없으면 응답 없이 턴을 끝내세요: 다시 쓰지 말고, 다시 쓰지 않았다는 말이나 이유도 쓰지 마세요. "
               "보고는 이미 화면 맨 아래에 있습니다.")
REASON = f"{DO_REASON} 그래서 보고가 바뀌면 바뀐 보고를 쓰세요. {END_QUIETLY}"
ASK_REASON = ("yoyoo-share-guard: 답이 허락을 묻는 말로 끝났습니다. 뜻이 분명하고 방법만 남았으면 추천안대로 지금 하세요. "
              f"prod에 닿는 배포·머지, 데이터, 외부 설정, 과금이거나 요청의 뜻이 모호해서 요유의 답이 꼭 필요한 경우가 아니면 묻지 않습니다. {END_QUIETLY}")
# 요유 2026-10-28 ("왜는 쓰는데 무엇을을 왜 안쓰지, 모든 세션이 다그러네"): every `왜:` label comes with a `무엇을:` label
WHY = re.compile(r"(?<![가-힣])왜[ \t]*\**[ \t]*[:：]")
WHAT = re.compile(r"무엇을[ \t]*\**[ \t]*[:：]")
WHAT_REASON = ("yoyoo-share-guard: 보고에 '왜:'는 있는데 짝이 되는 '무엇을:'이 없습니다. '왜:' 줄마다 바로 위에 "
               "'무엇을:' 줄(무엇이 바뀌었거나 바뀌는지, 파일·함수 이름 없이 쉬운 한국어)을 붙여 보고를 다시 쓰세요.")
# 요유 2026-11-01: a problem is reported with all three lines. This sees a report that dropped one, not one that used none:
# telling a problem report from any other needs a word list, and that kind of check is what made this hook noisy.
PROBLEM_LABELS = tuple(re.compile(r"(?<![가-힣])" + label + r"[ \t]*\**[ \t]*[:：]") for label in ("원인", "조치", r"재발[ \t]*방지"))
PROBLEM_REASON = ("yoyoo-share-guard: 문제 보고에 '원인:', '조치:', '재발 방지:' 중 빠진 줄이 있습니다. 문제마다 세 줄을 모두 쓰세요. "
                  "막을 방법이 없으면 '재발 방지: 없음'과 그 이유를 쓰세요.")
# Whole value of 요유 몫, for only_decisions. SHARE stops at the first `·` and auto-tab-close relies on it as it is.
SHARE_FULL = re.compile(r"요유[ \t]*몫[ \t]*\**[ \t]*[:：][ \t]*\**(.*?)[ \t]*(?=·[ \t]*\**(?:유요[ \t]*몫|세션)|$)", re.M)
ITEM = re.compile(r",|，|·|\s그리고\s")
ASIDE = re.compile(r"\([^)]*\)|（[^）]*）")  # the reason a session gives, and its commas, are not items
DECISION = re.compile(r"(?:결정(?:하기| 입력)?|정하기|고르기|선택|승인|답하기|판단|여부)$")
DELEGATED = re.compile(r"머지|맡기|공유|리뷰|검토|댓글")  # standing permission: these are 유요's own steps, however they are worded
# A step worded as a decision: `프로덕션 승격 승인` is 유요's step, or one bounce from it (요유 2026-11-01, api#975). A real 요유 call
# ends quietly at the question. `설계 승인` names no step and stays a decision.
STEP = re.compile(r"(?:배포|승격|반영|롤백|실행|재시작|설치|적용|전환|삭제|게시|전송|공지|로그인|진행)(?:할지|될지|을|를)?"
                  r"(?:[ \t]*(?:승인|결정(?:하기)?|정하기|고르기|선택|판단|여부|답하기))*$")
ONLY_YOYOO = re.compile(r"`/clear`|`prod 허용`")  # typed by 요유 alone: the handoff rule and the production unlock
ASK_BACK = re.compile(r"([가-힣])까요[ \t]*[?？]\**$")
PERMIT = re.compile(r"(?:해도[ \t]*(?:되나요|좋을까요|괜찮을까요)|하시겠습니까)[ \t]*[?？]\**$")
WHICH = re.compile(r"아니면|어떻게|어느|어떤|어디|언제|누구|무엇|뭐|뭘|까요[^?？]*까요")  # asks what to do, not leave to do it
CHOICE = re.compile(WHICH.pattern + r"|괜찮|(?:있|없|좋|맞|나)을까요")  # or whether it is right
PANE_ID = re.compile(r"(?<![A-Za-z0-9_:/.-])w[0-9A-Z]+:p[0-9A-Z]+(?![0-9A-Za-z])")  # ASCII edges: 에 may follow directly
PANE_MARKS = re.compile(r"^[\s🟢🔴👑🫡]+")
# The global rule's names for the three Macs: a bare 서버 맥 leaves 요유 guessing which one (요유 2026-10-24).
GOOD_MAC = re.compile(r"회사[ \t]*서버[ \t]*맥")
BAD_MAC = re.compile(r"상시[ \t]?맥(?!락)|always-on Mac|서버[ \t]?맥(?![북미락스])", re.I)
LAST_REPORT = re.compile(r"남은[ \t]*것[ \t]*\**[ \t]*[:：]")
TEST_PANE = "임시 "  # with the space: `임시저장 API 수정` is a work name
OPENED_BY = re.compile(r"^spawn-([0-9A-Za-z]+)-")  # pane-spawn's agent name: spawn-<caller's terminal>-<random>
WROTE_IT = re.compile(r"(?:Claude|유요).{0,12}(?:썼|작성)")
EXPLAIN_REASON = "yoyoo-share-guard: 이번 설명 페이지는 Codex가 실패해 Claude가 썼습니다. 보고에 'Claude가 썼습니다'와 실패 이유를 넣어 다시 쓰세요."
# 요유 2026-11-01: most reports go to Codex, as the pages do, since they could not follow Claude's. The rule to use a page was
# there and nothing held it: in the 14 days to 11/1, 13% of about 1,200 reports had a page and the median ran 621 characters of
# prose outside the labeled lines. At 500 about half go to Codex, 29 to 41 a day; a conclusion, three points and a path fit under it.
LONG_REPLY = 500
LABEL_LINE = re.compile(r"^\W*(?:무엇을|왜|남은[ \t]*것|원인|조치|재발[ \t]*방지)\W*[:：]")
PAGE = re.compile(r"/explain/\S+\.html")
LONG_REASON = ("yoyoo-share-guard: 답이 깁니다. 긴 보고는 Codex가 씁니다. html-explainer 스킬대로 사실을 파일에 적어 explain-page로 맡기고, "
               "터미널에는 publish가 출력한 Codex의 글과 페이지 경로, 라벨 줄(무엇을, 왜, 남은 것 등)만 남기세요.")


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


def misses_problem_line(text: str) -> bool:
    text = prose(text)
    return len({len(label.findall(text)) for label in PROBLEM_LABELS}) > 1


def leaves_yoyoo_work(text: str) -> bool:
    text = prose(text)
    for m in SHARE.finditer(text):
        value = m.group(1).strip(" \t*_.,;")
        if value and not NONE.match(value):
            return True
    return False


def pane_ids(text: str) -> list:
    """Inline code counts: that is where the ids leak. A fenced block is a log or a command."""
    return list(dict.fromkeys(PANE_ID.findall(STRIP[0].sub(" ", text))))


def herdr_list(env, kind: str) -> list:
    herdr = env.get("HERDR_BIN_PATH") or "herdr"
    try:
        out = subprocess.run([herdr, kind, "list"], capture_output=True, text=True, timeout=2).stdout
        return json.loads(out)["result"][f"{kind}s"]
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return []


def pane_name(pane: dict) -> str:
    return PANE_MARKS.sub("", unicodedata.normalize("NFC", pane.get("label") or ""))


def pane_names(env) -> dict:
    return {p["pane_id"]: pane_name(p) for p in herdr_list(env, "pane") if p.get("label")}


def opener_of(pane: dict, names: dict, env) -> str:
    """The terminal that opened a pane, or "": pane-spawn writes it into the agent name, pane-temp into a file."""
    opened = OPENED_BY.match(names.get(pane.get("pane_id")) or "")
    if opened:
        return f"term_{opened[1]}"
    folder = env.get("PANE_TEMP_DIR") or os.path.expanduser("~/.local/state/pane-temp")
    try:
        with open(os.path.join(folder, os.path.basename(pane.get("terminal_id") or "none")), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def left_open_reason(payload: dict, text: str, env) -> str | None:
    """The rewrite for panes this session opened and left open at a last report, or None.

    A test pane comes back whatever it is doing, any other pane once it stopped: 요유 talks only to the lead (2026-11-01),
    so reading, answering or closing a pane is never 요유 몫. A pane kept on purpose costs one rewrite: decide() asks once a turn.
    """
    if not LAST_REPORT.search(prose(text)):
        return None
    panes, session = herdr_list(env, "pane"), payload.get("session_id")
    me = next((p for p in panes if session and (p.get("agent_session") or {}).get("value") == session), None) or next(
        (p for p in panes if p.get("pane_id") == env.get("HERDR_PANE_ID")), None)  # a moved pane keeps an old HERDR_PANE_ID
    if not me or pane_name(me).startswith(TEST_PANE):
        return None
    names = {a.get("pane_id"): a.get("name") for a in herdr_list(env, "agent")}
    mine = [p for p in panes if p is not me and opener_of(p, names, env) == me.get("terminal_id")]
    tests = [p for p in mine if pane_name(p).startswith(TEST_PANE)]
    stopped = [p for p in mine if p not in tests and p.get("agent_status") != "working"]
    listed = lambda group: ", ".join(f"{pane_name(p)}({p.get('pane_id')})" for p in group)
    parts = []
    if tests:
        parts.append(f"yoyoo-share-guard: 이 세션이 띄운 시험용 칸이 아직 열려 있습니다: {listed(tests)}. `herdr pane close <ID>`로 닫고 보고를 다시 쓰세요.")
    if stopped:
        parts.append(f"yoyoo-share-guard: 이 세션이 띄운 칸이 멈춰 있습니다: {listed(stopped)}. 요유는 리드하고만 이야기하니 그 칸을 보거나 답하거나 닫는 일을 요유 몫으로 넘기지 마세요. "
                     "남은 일이 있으면 `pane-tell`로 시키고, 요유 결정이 필요하면 이 칸에서 묻고, 없으면 도는 작업이 없는지 확인한 뒤 `herdr pane close <ID>`로 닫고 보고를 다시 쓰세요.")
    return " ".join(parts) or None


def names_reason(text: str, env) -> str | None:
    """The rewrite for names 요유 cannot read, or None."""
    text = unicodedata.normalize("NFC", text)
    parts, ids = [], pane_ids(text)
    if ids:
        names = pane_names(env)
        listed = ", ".join(f"{i} = {names.get(i) or '지금 없는 칸이니 한 일의 이름으로'}" for i in ids)
        parts.append(f"보고에 칸 내부 ID가 있습니다. 요유는 칸 이름만 봅니다. ID를 지우고 칸 이름으로 적으세요({listed}).")
    macs = list(dict.fromkeys(BAD_MAC.findall(GOOD_MAC.sub(" ", prose(text)))))
    if macs:
        parts.append(f"맥은 회사맥, 회사서버맥, 집맥 가운데 하나로만 적으세요(고칠 말: {', '.join(macs)}).")
    return f"yoyoo-share-guard: {' '.join(parts)} 바뀐 곳만 고쳐 보고 전체를 다시 쓰세요." if parts else None


def explain_reason(payload: dict, text: str, env) -> str | None:
    """The rewrite for a page Claude wrote because Codex failed (html-explainer's explain-page leaves the mark), or None."""
    root = env.get("EXPLAIN_STATE") or os.path.expanduser("~/.local/state/explain")
    mark = os.path.join(root, "claude-wrote", os.path.basename(payload.get("session_id") or "none"))
    if not os.path.exists(mark):
        return None
    os.unlink(mark)  # once: the report is rewritten now or it never is
    return None if WROTE_IT.search(unicodedata.normalize("NFC", text)) else EXPLAIN_REASON


def stale_thread_reason(payload: dict, text: str, env) -> str | None:
    """The question for a report that says nobody answered on an old thread read: see slack-fresh-guard.py. Its own crash passes."""
    sys.dont_write_bytecode = True
    try:
        path = os.path.join(os.path.dirname(os.path.realpath(__file__)), "slack-fresh-guard.py")
        spec = importlib.util.spec_from_file_location("slack_fresh", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.report_reason(payload, prose(text), dict(env))
    except Exception:
        return None


def job_session(env) -> bool:
    """A job's report goes to the board and Slack, and no one reads it in the terminal."""
    return bool(env.get("MUNICE_AUTO_DISPATCH") or env.get("MUNICE_AMBIENT"))


def too_long(text: str) -> bool:
    """More prose than a conclusion, a few points and a path, with no page yet. The labeled closing lines stay in the terminal."""
    if PAGE.search(text):
        return False
    lines = (line.strip() for line in prose(text).splitlines())
    return sum(len(line) for line in lines if not LABEL_LINE.match(line)) > LONG_REPLY


def ends_in_rieul(syllable: str) -> bool:
    return "가" <= syllable <= "힣" and (ord(syllable) - 0xAC00) % 28 == 8


def asks_which(item: str) -> bool:
    """The question ending of `둘지`, `돌릴지`, `있는지`. Nouns end in 지 too (공지, 메시지), but not after a ㄹ final or 는/은/인."""
    before = item[-2:-1]
    return item.endswith("지") and (before in ("는", "은", "인") or ends_in_rieul(before))


def asks_back(text: str) -> bool:
    """The reply's last line outside the labeled ones asks leave for one step: `머지할까요?`, `진행해도 될까요?`."""
    lines = (line.strip() for line in reversed(prose(text).splitlines()))
    last = next((line for line in lines if line and not LABEL_LINE.match(line)), "")
    m = ASK_BACK.search(last)
    return (bool(PERMIT.search(last)) and not WHICH.search(last)) or (bool(m) and ends_in_rieul(m[1]) and not CHOICE.search(last))


def is_decision(item: str) -> bool:
    if ONLY_YOYOO.search(item):
        return True
    for pat in STRIP:
        item = pat.sub(" ", item)
    item = item.strip(" \t*_.,;")
    return (bool(DECISION.search(item)) or asks_which(item)) and not DELEGATED.search(item) and not STEP.search(item)


def only_decisions(text: str) -> bool:
    """Nothing in 요유 몫 is a step 유요 could take: asking would only print the report again.

    Model text cannot stop that: told to end silently, Codex rewrote it 2 of 2 and Claude left a note 2 of 3 (2026-10-31).
    ponytail: word endings, so "할지 결정" also passes a step 유요 should just take. Replayed on the 102 fires of 10/28-10/31
    this drops 42 of 77 idle fires and 10 of 25 that did work, about 5 of them real wins. Ask once per session if those come back.
    """
    text = STRIP[2].sub(" ", STRIP[0].sub(" ", unicodedata.normalize("NFC", text)))
    values = [ASIDE.sub(" ", m.group(1)) for m in SHARE_FULL.finditer(text)]
    items = [i.strip() for v in values if not NONE.match(v.strip(" \t*_.,;")) for i in ITEM.split(v) if i.strip(" \t*_.,;")]
    return bool(items) and all(is_decision(i) for i in items)


def decide(payload: dict, env, claude: bool = False) -> str | None:
    if payload.get("stop_hook_active") or env.get("CLAUDE_CODE_ENTRYPOINT", "").startswith("sdk"):
        return None
    text = payload.get("last_assistant_message")
    if text is None and payload.get("transcript_path"):
        text = final_text(payload["transcript_path"])  # unreadable transcript raises: fail open
    if not isinstance(text, str):
        return None
    checks = (names_reason(text, env), WHAT_REASON if misses_what(text) else None,
              PROBLEM_REASON if misses_problem_line(text) else None, left_open_reason(payload, text, env),
              explain_reason(payload, text, env), stale_thread_reason(payload, text, env),
              LONG_REASON if claude and not job_session(env) and too_long(text) else None)
    rewrites = [r for r in checks if r]
    left = leaves_yoyoo_work(text)
    if left and not only_decisions(text):
        rewrites.append(DO_REASON if rewrites else REASON)  # a rewrite that is due must not be told to stay silent
    elif not left and asks_back(text):
        rewrites.append(DO_REASON if rewrites else ASK_REASON)
    return " ".join(rewrites) or None


def selftest():
    import tempfile

    cli = {"CLAUDE_CODE_ENTRYPOINT": "cli", "HERDR_BIN_PATH": "/nonexistent/herdr"}  # never the live herdr of the pane running this

    def p(text, **kw):
        return {"hook_event_name": "Stop", "stop_hook_active": False, "last_assistant_message": text, **kw}

    block = [
        "배포했습니다.\n\n남은 것: 요유 몫: PR 머지 · 유요 몫: 없음 · 세션: 닫아도 됨",
        "남은 것: 요유 몫: Slack에 공지 올리기 · 유요 몫: watch 대기 · 세션: 닫아도 됨",
        "남은 것:\n- 요유 몫: staging QA 확인\n- 유요 몫: 없음\n- 세션: 닫아도 됨",
        "**요유 몫:** 배포 로그 확인",
        "남은 것: 요유 몫: 결정 하나, 위 스크립트 실행 · 유요 몫: 없음",  # one step 유요 could take is enough
        "남은 것: 요유 몫: #12 머지 승인 · 유요 몫: 없음",  # 유요 merges its own PRs
        "남은 것: 요유 몫: 저에게 맡기실지 답하기 · 유요 몫: 없음",
        "남은 것: 요유 몫: 프로덕션 승격 승인 · 유요 몫: 없음",  # a step worded as an approval (api#975)
        "남은 것: 요유 몫: prod 배포 승인 결정 · 유요 몫: 없음 · 세션: 닫아도 됨",
        "남은 것: 요유 몫: 프로덕션에 배포할지 결정 · 유요 몫: 없음",
        "남은 것: 요유 몫: prod 배포 결정하기 · 유요 몫: 없음",
        "남은 것: 요유 몫: 재시작할지 답하기 · 유요 몫: 없음",
        "남은 것: 요유 몫: 승격 여부 선택 · 유요 몫: 없음",
        "남은 것: 요유 몫: 프로덕션 승격을 승인 · 유요 몫: 없음",
        "남은 것: 요유 몫: 프로덕션 반영 승인 · 유요 몫: 없음",
        "남은 것: 요유 몫: prod 롤백 승인 · 유요 몫: 없음",
        "남은 것: 요유 몫: 재시작 여부 답하기 · 유요 몫: 없음",
        "남은 것: 요유 몫: 오늘 15시 진행 여부 답하기 · 유요 몫: 없음",
        "남은 것: 요유 몫: 합의한 Slack 공지 · 유요 몫: 없음",  # 지 in a noun is not a question ending
        "남은 것: 요유 몫: 팀원에게 보낼 메시지 · 유요 몫: 없음",
        "요유 몫: 없음 · 유요 몫: 없음\n\n남은 것: 요유 몫: 값 넣기 · 유요 몫: 없음",  # the last line counts too
        "요유 몫：태그 확인",  # full-width colon
        unicodedata.normalize("NFD", "남은 것: 요유 몫: 머지 · 유요 몫: 없음"),  # NFD text from a macOS paste
    ]
    allow = [
        "남은 것: 요유 몫: 없음 · 유요 몫: watch 대기 · 세션: 닫아도 됨",
        "남은 것: 요유 몫: 없음 · 유요 몫: 없음 · 세션: 닫아도 됨",
        "남은 것: 요유 몫: 페이지를 보고 훅을 만들지 결정 · 유요 몫: 페이지 작성 · 세션: 닫아도 됨",
        "남은 것: 요유 몫: 둘지, 지울지 결정(그대로 둬도 됨, 급하지 않음) · 유요 몫: 없음",
        "남은 것: 요유 몫: 지금 돌릴지(기본은 계속), 그리고 계정 결정 · 유요 몫: 없음",
        '남은 것: 요유 몫: "켜도 된다"고 답하기 · 유요 몫: 없음',
        "남은 것: 요유 몫: 세 길 중 하나 고르기 · 유요 몫: 없음",
        "남은 것: 요유 몫: 설계 승인 · 유요 몫: 구현 대기",  # a decision with no step in it
        "남은 것: 요유 몫: 훅을 만들지 결정 · 유요 몫: 없음",
        "남은 것: 요유 몫: 이 칸에서 `/clear` 입력 · 유요 몫: 없음",
        "남은 것: 요유 몫: 끌 때 `prod 허용` 한 번 · 유요 몫: 없음",
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
    assert decide(p(both), cli) == f"{WHAT_REASON} {DO_REASON}", "both checks in one block"
    for t in ("원인: 경로 오류\n조치: 경로 수정",  # 재발 방지 missing
              "**원인:** 경로 오류\n**재발 방지:** 테스트 추가",  # 조치 missing
              "조치：전각 콜론\n재발방지: 붙여 쓴 형태",  # 원인 missing
              "원인: a\n조치: b\n재발 방지: c\n\n원인: d\n조치: e"):  # the second problem is one line short
        assert decide(p(t), cli) == PROBLEM_REASON, f"should ask for the missing line: {t[:60]!r}"
    for t in ("원인: a\n조치: b\n재발 방지: c", "**원인:** a\n**조치:** b\n**재발 방지:** 없음 (외부 장애)",
              "- 원인: a · 조치: b · 재발방지: c", "`원인:` 줄을 쓰세요.", '"조치:" 줄만 쓰면 훅이 되묻습니다.',
              "장애 원인을 찾았고 조치했습니다.", "주요원인: 표의 머리글"):
        assert decide(p(t), cli) is None, f"should allow: {t[:60]!r}"
    # A report that left a decision is not rewritten: the question ends without a word when nothing changes.
    assert "쓰지 말고" in REASON and "다시 써" not in REASON, "the report stays where it is"
    # 요유 2026-11-01 (api#975): "shall I?" hands the step back as much as a 요유 몫 line does.
    for t in ("PR을 올렸습니다. 머지할까요?", "고쳤습니다.\n\n이대로 만들까요?", "진행해도 될까요?\n\n무엇을: 훅 수정\n왜: 막혀서",
              "스레드에 올려 둘까요？", "그렇게 할까요?\n남은 것: 요유 몫: 없음 · 유요 몫: 없음 · 세션: 닫아도 됨",
              "머지해도 되나요?", "머지해도 좋을까요?", "머지하시겠습니까?", "진행해도 괜찮을까요?"):
        assert decide(p(t), cli) == ASK_REASON, f"should ask back: {t[:40]!r}"
    for t in ("A로 갈까요, 아니면 B로 갈까요?", "어떻게 할까요?", "틀리거나 빠진 부분이 있을까요?", "어느 쪽이 좋을까요?",
              "`머지할까요?`라고 묻지 않습니다.", '"머지할까요?"처럼 쓰면 훅이 되묻습니다.', "머지할까요? 이 질문은 쓰지 않습니다.",
              "> 머지할까요?\n인용입니다.", "마지막 줄은 그냥 까요?", "머지했습니다.",
              "PR을 나눌까요, 합칠까요?", "A로 할까요 B로 할까요?", "어디에 둘까요?", "언제 배포할까요?", "뭐로 할까요?", "이대로 괜찮을까요?",
              "어떻게 하시겠습니까?", "어디에 배포하시겠습니까?"):
        assert decide(p(t), cli) is None, f"not a shall-I question: {t[:40]!r}"
    assert "prod에 닿는" in REASON, "a real production step is not something to go and do"
    assert decide(p("머지할까요?\n남은 것: 요유 몫: 세 길 중 하나 고르기 · 유요 몫: 없음"), cli) is None, "a decision on the 요유 몫 line is the handoff format"
    assert decide(p("머지할까요?\n남은 것: 요유 몫: 값 넣기 · 유요 몫: 없음"), cli) == REASON, "the 요유 몫 question already covers it"
    assert decide(p("왜: 막혀서\n머지할까요?"), cli) == f"{WHAT_REASON} {DO_REASON}", "a rewrite is due: no telling it to stay silent"
    assert decide(p("머지할까요?", stop_hook_active=True), cli) is None, "second stop of a turn must pass"

    with tempfile.TemporaryDirectory() as d:
        fake = os.path.join(d, "herdr")
        with open(fake, "w", encoding="utf-8") as f:
            f.write('#!/bin/sh\necho \'{"result":{"panes":[{"pane_id":"w5:pKR","label":"🟢 🫡 훅 구성 그림 설명"}]}}\'\n')
        os.chmod(fake, 0o755)
        with_herdr = {**cli, "HERDR_BIN_PATH": fake}
        for t in ("`w5:pKR`에 넘겼습니다.", "w5:pKR에 넘겼습니다.", "칸(`w5:pKR`)에서 진행합니다."):
            reason = decide(p(t), with_herdr)
            assert reason and "w5:pKR = 훅 구성 그림 설명" in reason, f"pane id must come back with its name: {t!r}"
        assert "지금 없는 칸" in (decide(p("`w9:p46`을 닫았습니다."), with_herdr) or ""), "a closed pane has no name"
        assert "wA:p7 =" in (decide(p("`wA:p7`에 넘겼습니다."), with_herdr) or ""), "workspace ids go on past 9 as letters"
        gone = {**cli, "HERDR_BIN_PATH": "/nonexistent/herdr"}
        assert "지금 없는 칸" in (decide(p("`w5:pKR`에 넘겼습니다."), gone) or ""), "herdr down: still asks, without a name"
    with tempfile.TemporaryDirectory() as d:
        fake, listing = os.path.join(d, "herdr"), os.path.join(d, "listing")
        with open(fake, "w", encoding="utf-8") as f:
            f.write(f'#!/bin/sh\ncat "{listing}.$1"\n')
        os.chmod(fake, 0o755)
        herdr_env = {**cli, "HERDR_BIN_PATH": fake}
        done = "끝났습니다.\n\n남은 것: 요유 몫: 없음 · 유요 몫: 없음 · 세션: 닫아도 됨"
        me = {"pane_id": "w1:p1", "tab_id": "w1:t1", "terminal_id": "term_aa", "agent_session": {"value": "s1"}, "label": "🟢 일"}
        test_pane = {"pane_id": "w1:p2", "tab_id": "w1:t2", "terminal_id": "term_bb", "label": "🟢 🫡 임시 시험 Claude", "agent_status": "working"}

        def herdr_has(panes, agents=()):
            with open(f"{listing}.pane", "w", encoding="utf-8") as f:
                json.dump({"result": {"panes": panes}}, f)
            with open(f"{listing}.agent", "w", encoding="utf-8") as f:
                json.dump({"result": {"agents": list(agents)}}, f)

        def ends(text=done):
            return decide(p(text, session_id="s1"), herdr_env)

        herdr_env["PANE_TEMP_DIR"] = d
        with open(os.path.join(d, "term_bb"), "w", encoding="utf-8") as f:
            f.write("term_aa\n")  # pane-temp's record of who opened it
        herdr_has([me, test_pane])
        assert "임시 시험 Claude(w1:p2)" in (ends() or ""), "a test pane left open comes back with what to close"
        herdr_has([me, test_pane], [{"pane_id": "w1:p2", "name": "spawn-aa-1"}])
        assert "임시 시험 Claude" in (ends() or ""), "a test pane this session opened through pane-spawn"
        os.unlink(os.path.join(d, "term_bb"))
        herdr_has([me, test_pane])
        assert ends() is None, "no record of who opened it: not this report's"
        herdr_has([me, test_pane], [{"pane_id": "w1:p2", "name": "spawn-cc-1"}])
        assert ends() is None, "another session's test pane is not this report's"
        with open(os.path.join(d, "term_bb"), "w", encoding="utf-8") as f:
            f.write("term_cc\n")
        herdr_has([me, test_pane])
        assert ends() is None, "nor is one pane-temp opened for another session"
        with open(os.path.join(d, "term_bb"), "w", encoding="utf-8") as f:
            f.write("term_aa\n")
        assert ends("시험을 계속합니다.") is None, "only the last report is held back"
        herdr_has([{**me, "label": "🟢 임시 시험 Codex"}, test_pane])
        assert ends() is None, "a test pane is not asked about its siblings"
        herdr_has([me, {**test_pane, "label": "🟢 칸 임시 정리"}])
        assert ends() is None, "the word in a work name is not a test pane"
        herdr_has([me, {**test_pane, "label": "🟢 임시저장 API 수정"}])
        assert ends() is None, "nor is a word that starts with it"
        herdr_has([me])
        assert ends() is None, "nothing left open"
        # 요유 2026-11-01: they talk only to the lead, so a stopped pane is the lead's to finish and close, never 요유 몫.
        worker = {"pane_id": "w1:p3", "tab_id": "w1:t2", "terminal_id": "term_dd", "label": "🔴 🫡 구현", "agent_status": "idle"}
        opened = [{"pane_id": "w1:p3", "name": "spawn-aa-1"}]
        herdr_has([me, worker], opened)
        assert "구현(w1:p3)" in (ends() or "") and "요유 몫" in (ends() or ""), "a stopped pane this session opened comes back"
        herdr_has([me, {**worker, "agent_status": "done"}], opened)
        assert "구현(w1:p3)" in (ends() or ""), "done is stopped too"
        herdr_has([me, {**worker, "agent_status": "unknown"}], opened)
        assert "구현(w1:p3)" in (ends() or ""), "so is any status but working"
        herdr_has([me, {**worker, "agent_status": "working"}], opened)
        assert ends() is None, "a pane still working is not left over"
        herdr_has([me, worker], [{"pane_id": "w1:p3", "name": "spawn-cc-1"}])
        assert ends() is None, "another lead's pane is not this report's"
        herdr_has([me, worker], opened)
        assert ends("계속 진행합니다.") is None, "only a last report is held back"
        herdr_has([{**me, "label": "🟢 임시 시험"}, worker], opened)
        assert ends() is None, "a test pane answers for nothing"
        moved = {**me, "pane_id": "w2:p9"}
        herdr_has([moved, test_pane])
        assert ends(), "a moved pane is found by its session"
        with open(f"{listing}.pane", "w", encoding="utf-8") as f:
            f.write("not json")
        assert ends() is None, "a broken herdr passes"
    for t in ("```\nherdr pane get w5:pKR\n```\n로그입니다.", "w5:pane 형식", "wA:pane 형식", "https://x.dev/w5:pKR", "tw5:pKR"):
        assert decide(p(t), cli) is None, f"not a pane id in prose: {t!r}"
    for t in ("상시 맥에서 돕니다.", "상시맥을 씁니다.", "always-on Mac에서 돕니다.", "서버맥에서 받았습니다.", "서버 맥에 둡니다."):
        assert "회사서버맥" in (decide(p(t), cli) or ""), f"machine name must come back: {t!r}"
    for t in ("회사서버맥에서 돕니다.", "회사 서버맥에서 돕니다.", "회사맥과 집맥입니다.", "서버 맥북을 씁니다.", "서버 맥락을 봅니다.", "상시 맥락입니다.",
              "`서버맥`은 쓰지 않습니다.", '"상시 맥"은 금지입니다.'):
        assert decide(p(t), cli) is None, f"machine name is fine: {t!r}"
    assert (decide(p("`w5:pKR` 확인\n남은 것: 요유 몫: 값 넣기 · 유요 몫: 없음"), cli) or "").endswith(DO_REASON), \
        "a rewrite for a name keeps the 요유 몫 question without telling it to stay silent"
    assert leaves_yoyoo_work("남은 것: 요유 몫: 결정 하나 · 유요 몫: 없음"), "auto-tab-close keeps a pane with a decision left"

    with tempfile.TemporaryDirectory() as d:
        wrote = {**cli, "EXPLAIN_STATE": d}
        mark = os.path.join(d, "claude-wrote", "s9")
        os.makedirs(os.path.dirname(mark))

        def after_fallback(text):
            with open(mark, "w", encoding="utf-8") as f:
                f.write("page")
            return decide(p(text, session_id="s9"), wrote)

        assert "Claude가 썼" in (after_fallback("페이지를 띄웠습니다.") or ""), "a page Claude wrote must be named in the report"
        assert not os.path.exists(mark) and decide(p("다른 일입니다.", session_id="s9"), wrote) is None, "asked once"
        for t in ("Codex 로그인이 만료되어 이 페이지는 Claude가 썼습니다.", "Codex가 실패해 유요가 대신 썼습니다.", "Claude가 작성했습니다."):
            assert after_fallback(t) is None, t
        assert decide(p("페이지를 띄웠습니다.", session_id="other"), wrote) is None, "another session's page is not this report's"
        assert decide(p("페이지를 띄웠습니다."), wrote) is None, "no session id, nothing to read"

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

    # 요유 2026-11-01: most reports go to Codex, as the pages do. Only prose outside the labeled closing lines counts.
    wall, under = "가" * (LONG_REPLY + 1), "가" * LONG_REPLY
    assert decide(p(wall), cli, claude=True) == LONG_REASON, "a long reply in a Claude session goes to Codex"
    assert decide(p(under), cli, claude=True) is None, "up to the limit stays in the terminal"
    assert decide(p(wall), cli) is None, "a Codex session writes its own reports"
    closing = "\n".join(f"{label}: {under}" for label in ("무엇을", "왜", "원인", "조치", "재발 방지", "남은 것"))
    assert decide(p("끝났습니다.\n" + closing), cli, claude=True) is None, "the labeled lines stay in the terminal"
    assert decide(p(f"**무엇을:** {wall}"), cli, claude=True) is None, "a bold label is a label"
    assert decide(p(f"{wall}\n/Users/yoyoo/explain/2026-11-01-a.html"), cli, claude=True) is None, "a page already went to Codex"
    assert decide(p(f"```\n{wall}\n```"), cli, claude=True) is None, "code is not prose"
    for job in ("MUNICE_AUTO_DISPATCH", "MUNICE_AMBIENT"):
        assert decide(p(wall), {**cli, job: "1"}, claude=True) is None, "a job's report goes to the board and Slack"
    assert decide(p(wall, stop_hook_active=True), cli, claude=True) is None, "sent back once at most"
    assert decide(p(f"{wall}\n남은 것: 요유 몫: 값 넣기 · 유요 몫: 없음"), cli, claude=True) == f"{LONG_REASON} {DO_REASON}", \
        "the rewrite is due, so the 요유 몫 question must not tell it to stay silent"
    code, out = run(json.dumps(p(wall)), args=["--claude"])
    assert json.loads(out)["hookSpecificOutput"]["additionalContext"] == LONG_REASON, out
    assert run(json.dumps(p(wall))) == (0, ""), "without --claude the reply passes"
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
        sys.exit()
    try:
        reason = decide(json.load(sys.stdin), os.environ, "--claude" in sys.argv)
    except Exception:  # never break a session
        sys.exit(0)
    if reason and "--claude" in sys.argv:
        # Same once-per-turn continuation as a block (stop_hook_active is set), shown as feedback, not an error.
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": reason}}, ensure_ascii=False))
    elif reason:
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
