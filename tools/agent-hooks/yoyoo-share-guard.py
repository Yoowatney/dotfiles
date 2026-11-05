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
A session is asked about each 요유 몫 item once (asked_before): an open decision rides in every report until 요유 answers.
A report whose 요유 몫 holds only decisions is not asked at all (only_decisions); auto-tab-close still sees it as work left.
It also sends back names 요유 cannot read: a pane's herdr id (`w5:pKR`, 27 reports in 14 days; the reason carries the
pane's name) and the machine names the global rule bans. A last report (it has `남은 것:`) also comes back while a test pane
this session opened (`임시 …`) is open: 4 of them stayed idle after this hook's own tests (10/31). A report after an explainer
page Claude had to write itself because Codex failed comes back unless it says so (요유 2026-10-31). In a Claude session a
long reply with no page comes back and goes to Codex (요유 2026-11-01); a Codex session writes its own.
The first last report after a pane's report reached this session (auto-tab-close's message, read from the session record)
comes back without a `확인:` line and a command run since that is not a pane chore: a lead passing a pane's "done" on unchecked
(요유 2026-11-04). It comes before the failure: in 53 such reports by then (tests and other leads' panes left out), a fresh
review found no lead that passed a "done" on unchecked.
A pane that hands checks out (herdr_peers.delegates_checks: the main pane today, 요유 2026-11-05) shows a check pane instead of a
command of its own; relay_reason says how. Any other pane, a missing or stale main-pane file and a broken herdr get the rule above.
In the main pane alone, any turn also comes back while 요유's Slack words the relay passed there have no reply after them
(owed_reason, from the relay's local state).
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
import time
import unicodedata

sys.dont_write_bytecode = True  # no __pycache__ in the dotfiles at every stop

STRIP = [
    re.compile(r"(```|~~~)[\s\S]*?(?:\1|\Z)"),  # fenced code, also unclosed
    re.compile(r"`[^`\n]*`"),  # inline code
    re.compile(r"^[ \t]*>[^\n]*", re.M),  # blockquote lines
    re.compile(r'"[^"\n]*"|“[^”\n]*”|‘[^’\n]*’|「[^」\n]*」'),  # quoted text
]
SHARE = re.compile(r"요유[ \t]*몫[ \t]*\**[ \t]*[:：][ \t]*\**([^·\n]*)")  # horizontal space only: never the next line
NONE = re.compile(r"^(?:없음|없습니다|none)\b(?![ \t]*[,，])", re.I)  # "없음, 시험 실행" still lists work
# Short, since Claude prints it under the report. A note such as "그대로 둡니다" would bury the report (요유 2026-10-28),
# and a rewrite prints it twice (요유 2026-10-31), so a turn with nothing to do ends without a word.
# The aside: on 10/29 a session answered this question by adding why only 요유 could do it, and 유요 had another way (api#975).
DO_REASON = ("yoyoo-share-guard: 요유 몫 가운데 유요가 지금 할 수 있는 일(명령 실행, Slack 글, 합의된 단계)은 지금 하세요. "
             "막혔다는 사유를 붙여 남기지 말고 다른 경로를 찾으세요. 다만 권한 규칙이 거부한 명령은 형태를 바꿔 다시 실행하지 말고 그대로 보고한다. "
             "prod에 닿는 배포·머지, 데이터, 외부 설정, 과금은 요유의 확인이 있어야 하니 하지 말고 그대로 두세요.")
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
# But an aside that adds work holds more items: "설계 승인(그리고 시험 실행)", "없음(새 작업: 시험 실행)" (2026-11-05).
# ponytail: its opening words only; one that adds work without them ("없음(시험은 돌려야 함)") still reads as a reason.
ADDED = re.compile(r"[(（][ \t]*(?:그리고[ \t]+|새[ \t]*작업[ \t]*[:：][ \t]*)([^)）]*)[)）]")
RECORD = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.I)  # a session id, as Claude and Codex name them
DECISION = re.compile(r"(?:결정(?:하기| 입력)?|정하기|고르기|선택|승인|답하기|판단|여부)$")
DELEGATED = re.compile(r"머지|맡기|공유|리뷰|검토|댓글")  # standing permission: these are 유요's own steps, however they are worded
# A step worded as a decision: `프로덕션 승격 승인` is 유요's step, or one bounce from it (요유 2026-11-01, api#975). A real 요유 call
# ends quietly at the question. `설계 승인` names no step and stays a decision.
# 시작/착수/답 (요유 2026-11-04, "watch하라는건 이 스레드가 알아서 작업하라는거임"): `작업을 지금 시작할지 정하기` and
# `팀원에게 답할지 정하기` passed as 요유 calls. Replayed on every past report: 4 flipped, and 요유 had answered go to all 4.
STEP = re.compile(r"(?:배포|승격|반영|롤백|실행|시작|착수|답할지|설치|적용|전환|삭제|게시|전송|공지|로그인|진행)(?:할지|될지|을|를)?"
                  r"(?:[ \t]*(?:승인|결정(?:하기)?|정하기|고르기|선택|판단|여부|답하기))*$")
ONLY_YOYOO = re.compile(r"`/clear`|`prod 허용`|`staging 허용`")  # typed by 요유 alone: the handoff rule and the GCP unlocks
ASK_BACK = re.compile(r"([가-힣])까요[ \t]*[?？]\**$")
PERMIT = re.compile(r"(?:해도[ \t]*(?:되나요|좋을까요|괜찮을까요)|하시겠습니까)[ \t]*[?？]\**$")
WHICH = re.compile(r"아니면|어떻게|어느|어떤|어디|언제|누구|무엇|뭐|뭘|까요[^?？]*까요")  # asks what to do, not leave to do it
CHOICE = re.compile(WHICH.pattern + r"|괜찮|(?:있|없|좋|맞|나)을까요")  # or whether it is right
PANE_ID = re.compile(r"(?<![A-Za-z0-9_:/.-])w[0-9A-Z]+:p[0-9A-Z]+(?![0-9A-Za-z])")  # ASCII edges: 에 may follow directly
PANE_MARKS = re.compile(r"^[\s🟢🔴👑🫡]+")
# The global rule's names for the three Macs: a bare 서버 맥 leaves 요유 guessing which one (요유 2026-10-24).
GOOD_MAC = re.compile(r"회사[ \t]*서버[ \t]*맥")
BAD_MAC = re.compile(r"상시[ \t]?맥(?!락)|always-on Mac|서버[ \t]?맥(?![북미락스])", re.I)
# 요유 2026-11-04 (api#993, "깃헙들어가서 히스토리 하나하나 읽어야해"): a 요유 몫 item that only points at where the
# question is (a link, an issue number, the board, "위") names no decision. The item must say what to choose.
POINTER_ITEM = re.compile(r"^(?:(?:https?://\S+|\S*#\d+|보드|이슈|항목|댓글|위|이)[ \t]*(?:의|에|에서)?[ \t]*)+"
                          r"(?:질문|댓글|코멘트|결정|선택지|내용)(?:에|을|를|의)?[ \t]*"
                          r"(?:결정|답하기|답|확인|보기|읽기|검토|고르기|하기)?$")
BARE_QUESTION = re.compile(r"^(?:질문|댓글|코멘트)(?:에|을|를)?[ \t]*(?:답하기|답|확인|보기|읽기)?$")
POINTER_REASON = ("yoyoo-share-guard: 요유 몫이 링크나 보드 질문만 가리킵니다. 요유는 GitHub에서 기록을 읽지 않습니다. "
                  "요유 몫에 결정할 내용을 한 줄로 적어 보고를 다시 쓰세요: 무엇을 고르는지, 추천안, 고르면 무엇이 바뀌는지.")
LAST_REPORT = re.compile(r"남은[ \t]*것[ \t]*\**[ \t]*[:：]")
TEST_PANE = "임시 "  # with the space: `임시저장 API 수정` is a work name
OPENED_BY = re.compile(r"^spawn-([0-9A-Za-z]+)-")  # pane-spawn's agent name: spawn-<caller's terminal>-<random>
WROTE_IT = re.compile(r"(?:Claude|유요).{0,12}(?:썼|작성)")
EXPLAIN_REASON = "yoyoo-share-guard: 이번 설명 페이지는 Codex가 실패해 Claude가 썼습니다. 보고에 'Claude가 썼습니다'와 실패 이유를 넣어 다시 쓰세요."
# 요유 2026-11-01: most reports go to Codex, as the pages do, since they could not follow Claude's. The rule to use a page was
# there and nothing held it: in the 14 days to 11/1, 13% of about 1,200 reports had a page and the median ran 621 characters of
# prose outside the labeled lines. At 500 about half go to Codex, 29 to 41 a day; a conclusion, three points and a path fit under it.
LONG_REPLY = 500
LABEL_LINE = re.compile(r"^\W*(?:무엇을|왜|확인|남은[ \t]*것|원인|조치|재발[ \t]*방지)\W*[:：]")
# 요유 2026-11-04: in a talk on multi-agent setups a lead passed a sub-agent's "done" on to the person unchecked, and the feature
# did not work. Here the verification rule covered only changes, and nothing held a lead to it. auto-tab-close sends a pane's
# report up with RELAY_NOTE; the first last report after one arrived needs a `확인:` line and a command run since it arrived.
RELAY_NOTE = ("요유나 위 칸에 올리기 전에 결과를 이 칸에서 직접 확인하세요(테스트·명령을 다시 실행하거나 바뀐 동작·데이터를 직접 보기). "
              "아래는 하위 칸의 주장입니다. 보고에는 무엇을 직접 확인했고 결과가 어땠는지 '확인:' 줄로 적으세요")
RELAYED = re.compile(r"(?:^|\] )(이|확인) 칸의 (?:마지막 )?보고입니다\(", re.M)  # auto-tab-close's heads, after pane-tell's mark
CHECK_LINE = re.compile(r"(?<![가-힣])확인[ \t]*\**[ \t]*[:：][ \t]*\**([^\n]*)")
RELAY_REASON = ("yoyoo-share-guard: 하위 칸의 보고를 받은 뒤 처음 쓰는 보고인데, 이 칸에서 결과를 직접 확인한 흔적이 없습니다. "
                "하위 칸의 '다 했다'는 주장일 뿐입니다. 지금 도구로 직접 확인하세요: 테스트·명령을 다시 실행하거나 바뀐 동작·데이터를 직접 보세요. "
                "하위 칸 보고를 다시 읽거나 칸을 닫는 일은 확인이 아닙니다. 그런 다음 무엇을 확인했고 결과가 어땠는지 '확인:' 줄을 넣어 보고를 다시 쓰세요.")
# 요유 2026-11-05 (api#1002): the main pane's turn is what 요유's next words wait behind, so a pane that hands checks out
# (herdr_peers.delegates_checks) gives the check to a fresh pane and writes its `확인:` line from what that pane ran and saw. The check
# pane's report ends the round. The wording names no pane kind, so widening the gate changes no text.
HOW_TO_CHECK = ("`pane-spawn --check \"<일 이름>\" \"<실행할 테스트·명령이나 볼 동작·데이터, 전체 보고 파일>\"`로 일을 한 칸이 아닌 새 확인 칸을 여세요. "
                "확인 칸의 보고가 오면 그 칸이 실행하고 본 것을 '확인:' 줄에 적고 확인 칸이 했다고 밝히세요.")
DELEGATE_NOTE = (f"이 칸은 결과를 직접 확인하지 않습니다. 아래는 하위 칸의 주장입니다. 요유나 위 칸에 올리기 전에 {HOW_TO_CHECK} "
                 "확인할 결과가 없는 보고(질문, 결정 요청)는 확인 칸 없이 처리하세요")
CHECK_NOTE = ("아래는 확인 칸이 실행하고 본 결과입니다. 확인 칸을 또 열지 말고, 그 칸이 실행하고 본 것을 '확인:' 줄에 적고 확인 칸이 했다고 밝히세요. "
              "통과하지 못했으면 일을 맡은 칸이나 새 칸에 고치라고 넘기세요")
DELEGATE_REASON = (f"yoyoo-share-guard: 하위 칸의 보고를 받은 뒤 처음 쓰는 보고인데, 맡긴 확인 칸이 없습니다. 이 칸은 결과를 직접 확인하지 않으며, 직접 돌린 것은 확인으로 치지 않습니다. {HOW_TO_CHECK} "
                   "확인할 결과가 없는 보고(질문, 결정 요청)였으면 응답 없이 턴을 끝내세요.")
CHECK_REPORT_REASON = ("yoyoo-share-guard: 확인 칸의 보고를 받은 뒤 처음 쓰는 보고인데, 확인 칸의 결과를 적은 '확인:' 줄이 없습니다. 확인 칸을 또 열지 마세요. "
                       "그 칸이 실행하고 본 것을 적고 확인 칸이 했다고 밝힌 '확인:' 줄을 넣어 보고를 다시 쓰세요.")
# 요유 2026-11-05: dispatch tabs run on the hidden workers server, and only what needs 요유 reaches the main pane. It is no claim of work
# done, so it asks for no check pane: this head stays out of RELAYED, and it keeps the main pane's turn short, since 요유's next
# words wait behind it. Nothing can be typed into that pane, and it has closed by then, so the board and a new pane carry the rest.
JOB_NOTE = ("자동 칸이 요유의 결정이나 손을 기다립니다. 결과 보고가 아니니 확인 칸을 열지 마세요. 질문은 그 칸의 보드 항목에도 있습니다. "
            "바로 처리할 일이 아니면 모아 둔 결정에 더하고 턴을 짧게 끝내세요. 그 칸은 입력을 받지 않으니 이어 갈 일은 새 칸에 맡기고, "
            "production 단계는 메인 칸에서 직접 하세요")
CHECK_AGENT = re.compile(r"^spawn-[0-9A-Za-z]+-chk")  # pane-spawn --check's agent name, which a pane rename does not change
BY_CHECK_PANE = re.compile(r"확인[ \t]*칸")
SPAWN_CHECK = re.compile(r"(?:^|[;&|(\n])\s*(?:[A-Za-z_]\w*=\S*\s+)*(?:\S*/)?pane-spawn(?:\s+--[\w-]+(?:\s+[^\s\"'-]\S*)?)*?\s+--check\b")
QUOTED = re.compile(r"\"(?:\\.|[^\"\\])*\"|'[^']*'")
# pane-spawn prints the pane alone on a line once it has opened, and starts each of its error lines, "shows no work" too, with its name (in
# Codex's JSON a line starts at "output" or after an escaped newline). A check pane counts only on such an address with no such error line
# in the call's output, so a later step that fails takes nothing back. ponytail: output only, so a later step printing a line that starts
# `pane-spawn: ` reads as a failed spawn and costs one more check pane; only reading the command could tell them apart.
LINE_START = r"(?:^|\\n|\"output\":\s*\")"
PANE_ADDRESS = re.compile(LINE_START + r"(?:\w+/)?w\w+:p\w+(?:$|\\n|\")", re.M)
SPAWN_ERROR = re.compile(LINE_START + r"pane-spawn: ", re.M)
# Calls that handle panes, read the pane's own claim or hand the check to another agent (whose word is a claim again). ponytail:
# a word list, so `ls` still counts as a check; it stops a lead that ran nothing, not one that ran too little.
NOT_A_CHECK = {"TaskCreate", "TaskUpdate", "TaskList", "TaskGet", "TaskStop", "ToolSearch", "Skill", "SendMessage", "Write", "Edit",
               "MultiEdit", "NotebookEdit", "ScheduleWakeup", "CronCreate", "CronDelete", "mcp__task-progress__progress", "Agent",
               "Task", "update_plan", "apply_patch", "spawn_agent", "send_message", "followup_task", "list_agents", "sleep",
               "request_user_input_async"}
# A command is judged by the first stage of its pipeline (`herdr … | head` is still a chore), heredoc bodies are text, and a plain
# assignment runs nothing. ponytail: a regex reading of shell, as in auto-tab-close.
COMMANDS = re.compile(r"&&|\|\||;|\n")
CHORE_STEP = re.compile(r"^\s*(?:\w+=[\"']?\$\(\s*)?(?:\S*/)?(?:cd|echo|printf|true|sleep|pane-label|pane-tell|pane-spawn|pane-temp|herdr)\b"
                        r"|pane-reports|^\s*\w+=[^\s$`]*\s*$")
HEREDOC = re.compile(r"<<-?[ \t]*(['\"]?)(\w+)\1[^\n]*\n.*?^[ \t]*\2[ \t]*$", re.S | re.M)
# Codex runs commands as tools.exec_command({cmd:"…"}), with single quotes too, or as JSON arguments {"cmd": "…"}.
CODEX_CMD = re.compile(r"\bcmd[\"']?\s*:\s*(\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')")
# A call a hook or a permission rule stopped never ran: Claude's error result, Codex's output text. A test that ran and failed counts.
REFUSED = re.compile(r"PreToolUse:\S+ hook error|blocked by PreToolUse hook|Permission to use \S+ has been denied|doesn't want to proceed")
PAGE = re.compile(r"/explain/\S+\.html")
# Lead 2026-11-05: the main pane left 요유's Slack words unanswered twice that day (tf#1083's '머지 ㄱㄱ' went to a pane with no
# reply in the thread), and done --thread, the one check, runs only when a thread is closed. So each turn of the main pane ends
# with the threads still owed a reply, read from the relay's own state (owed_reason).
OWED_REASON = ("yoyoo-share-guard: 릴레이가 이 칸에 넘긴 요유의 Slack 글 가운데, 뒤에 우리 답글이 없는 스레드가 있습니다: {}. "
               "턴을 끝내기 전에 스레드마다 `main-channel.py post --thread <스레드 ts>`로 답하세요. 일이 이어지면 어디까지 했고 끝나면 "
               "그 스레드에 알린다고 쓰고, 끝난 일이면 답한 뒤 `main-channel.py done --thread <스레드 ts>`로 닫으세요. "
               "이미 다른 길로 답했으면 릴레이가 1분 안에 그 답을 읽고 목록에서 지우니, 응답 없이 턴을 끝내세요.")
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
        value = ADDED.sub(r", \1", m.group(1)).strip(" \t*_.,;")
        if value and not NONE.match(value):
            return True
    return False


def pane_ids(text: str) -> list:
    """Inline code counts: that is where the ids leak. A fenced block is a log or a command."""
    return list(dict.fromkeys(PANE_ID.findall(STRIP[0].sub(" ", text))))


def herdr_list(env, kind: str, socket: str = "") -> list:
    """`herdr <kind> list` of this pane's server, or of the server at `socket`. [] on any failure."""
    herdr = env.get("HERDR_BIN_PATH") or "herdr"
    e = {**os.environ, **env, "HERDR_SOCKET_PATH": socket} if socket else {**os.environ, **env}
    try:
        out = subprocess.run([herdr, kind, "list"], capture_output=True, text=True, timeout=2, env=e).stdout
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


def my_pane(panes: list, payload: dict, env) -> dict | None:
    session = payload.get("session_id")
    return next((p for p in panes if session and (p.get("agent_session") or {}).get("value") == session), None) or next(
        (p for p in panes if p.get("pane_id") == env.get("HERDR_PANE_ID")), None)  # a moved pane keeps an old HERDR_PANE_ID


def agent_names(env) -> dict:
    return {a.get("pane_id"): a.get("name") for a in herdr_list(env, "agent")}


def under_lead(payload: dict, env) -> bool:
    """A pane pane-spawn opened and 요유 did not make a lead: its report goes to the lead that opened it, not to 요유."""
    me = my_pane(herdr_list(env, "pane"), payload, env)
    return bool(me) and "👑" not in (me.get("label") or "") and bool(OPENED_BY.match(agent_names(env).get(me.get("pane_id")) or ""))


def peers_module():
    spec = importlib.util.spec_from_file_location(
        "herdr_peers", os.path.join(os.path.dirname(os.path.realpath(__file__)), "herdr_peers.py"))
    peers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(peers)
    return peers


def delegates_checks(payload: dict, env) -> bool:
    """herdr_peers decides. A pane herdr cannot name, or any failure, is the ordinary rule."""
    try:
        me = my_pane(herdr_list(env, "pane"), payload, env) or {}
        return peers_module().delegates_checks(env, me.get("terminal_id"))
    except Exception:
        return False


def workers_socket(env, me: dict) -> str:
    """The workers server's socket, where pane-spawn opens a lead's panes, or "". Fails open."""
    try:
        if not me.get("terminal_id"):
            return ""
        sock = peers_module().socket_of(env, "workers")
        return "" if sock == env.get("HERDR_SOCKET_PATH") else sock  # a pane on workers itself: already read
    except Exception:
        return ""


def left_open_reason(payload: dict, text: str, env) -> str | None:
    """The rewrite for panes this session opened and left open at a last report, or None.

    A test pane comes back whatever it is doing, any other pane once it stopped: 요유 talks only to the lead (2026-11-01),
    so reading, answering or closing a pane is never 요유 몫. A pane kept on purpose costs one rewrite per stop, not one each turn.
    """
    if not LAST_REPORT.search(prose(text)):
        return None
    panes = herdr_list(env, "pane")
    me = my_pane(panes, payload, env)
    if not me or pane_name(me).startswith(TEST_PANE):
        return None
    # (address prefix, pane): each server's panes matched with that server's agent names, so a pane id on both never mixes
    agents = {"": {a.get("pane_id"): a for a in herdr_list(env, "agent")}}
    names = {k: a.get("name") for k, a in agents[""].items()}
    mine = [("", p) for p in panes if p is not me and opener_of(p, names, env) == me.get("terminal_id")]
    workers = workers_socket(env, me)
    if workers:
        agents["workers/"] = {a.get("pane_id"): a for a in herdr_list(env, "agent", workers)}
        names = {k: a.get("name") for k, a in agents["workers/"].items()}
        mine += [("workers/", p) for p in herdr_list(env, "pane", workers) if opener_of(p, names, env) == me.get("terminal_id")]
    tests = [(s, p) for s, p in mine if pane_name(p).startswith(TEST_PANE)]
    stopped = [(s, p) for s, p in mine if (s, p) not in tests and p.get("agent_status") != "working"]
    # Each stop once a session (요유 2026-11-05): herdr's state_change_seq moves when the pane works again
    stop = lambda s, p: f"stop {s}{p.get('terminal_id') or p.get('pane_id')}:{agents[s].get(p.get('pane_id'), {}).get('state_change_seq', '')}"
    new = remember(payload, env, {stop(s, p) for s, p in stopped})
    stopped = [(s, p) for s, p in stopped if stop(s, p) in new]
    listed = lambda group: ", ".join(f"{pane_name(p)}({s}{p.get('pane_id')})" for s, p in group)
    close = lambda group: "`herdr pane close <ID>`" + (
        "(workers 칸은 `herdr --session workers pane close <ID>`)" if any(s for s, _ in group) else "")
    parts = []
    if tests:
        parts.append(f"yoyoo-share-guard: 이 세션이 띄운 시험용 칸이 아직 열려 있습니다: {listed(tests)}. {close(tests)}로 닫고 보고를 다시 쓰세요.")
    if stopped:
        parts.append(f"yoyoo-share-guard: 이 세션이 띄운 칸이 멈춰 있습니다: {listed(stopped)}. 요유는 리드하고만 이야기하니 그 칸을 보거나 답하거나 닫는 일을 요유 몫으로 넘기지 마세요. "
                     f"남은 일이 있으면 `pane-tell`로 시키고, 요유 결정이 필요하면 이 칸에서 묻고, 없으면 도는 작업이 없는지 확인한 뒤 {close(stopped)}로 닫고 보고를 다시 쓰세요.")
    return " ".join(parts) or None


def unquote(literal: str) -> str:
    if literal.startswith("'"):
        return literal[1:-1].replace("\\'", "'")
    try:
        return json.loads(literal)
    except ValueError:  # a JS escape JSON lacks
        return literal[1:-1]


def flat(content) -> str:
    return content if isinstance(content, str) else " ".join(
        b.get("text") or "" for b in content or [] if isinstance(b, dict)) if isinstance(content, list) else ""


def pane_opened(output: str) -> bool:
    return bool(PANE_ADDRESS.search(output)) and not SPAWN_ERROR.search(output)


def records(lines):
    """(kind, text, tool name, call id) from Claude or Codex session record lines, oldest first. kind is "in", "say", "call",
    "refused" for a call a hook or a permission rule stopped, or "opened" for one whose output shows a pane it opened. Other tool output yields nothing."""
    for raw in lines:
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        p = d.get("payload") if isinstance(d.get("payload"), dict) else {}
        queued = d.get("attachment") if isinstance(d.get("attachment"), dict) else {}
        kind, content = d.get("type"), (d.get("message") or {}).get("content")
        results = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"] if isinstance(content, list) else []
        if kind == "attachment" and queued.get("type") == "queued_command":
            yield "in", str(queued.get("prompt") or ""), "", ""  # typed while the agent worked
        elif kind == "user" and results:
            for b in results:
                output = flat(b.get("content"))
                if b.get("is_error") and REFUSED.search(output):
                    yield "refused", "", "", b.get("tool_use_id") or ""
                elif pane_opened(output):
                    yield "opened", "", "", b.get("tool_use_id") or ""
        elif kind == "user" and content:
            yield "in", flat(content), "", ""
        elif kind == "assistant" and not d.get("isSidechain"):
            for b in content if isinstance(content, list) else []:
                inp = b.get("input") if isinstance(b, dict) and isinstance(b.get("input"), dict) else {}
                if isinstance(b, dict) and b.get("type") == "text":
                    yield "say", b.get("text") or "", "", ""
                elif isinstance(b, dict) and b.get("type") == "tool_use":
                    command = inp.get("command")
                    command = command if isinstance(command, str) else json.dumps(inp, ensure_ascii=False)
                    yield "call", command, b.get("name") or "", b.get("id") or ""
        elif kind == "response_item" and p.get("type") == "message" and p.get("role") in ("user", "assistant"):
            yield ("in" if p["role"] == "user" else "say"), flat(p.get("content")), "", ""
        elif kind == "response_item" and p.get("type") in ("function_call", "custom_tool_call", "local_shell_call"):
            body = str(p.get("arguments") or p.get("input") or p.get("action") or "")
            commands = [unquote(c) for c in CODEX_CMD.findall(body)]
            yield "call", "\n".join(commands) if commands else body, p.get("name") or "", p.get("call_id") or ""
        elif kind == "response_item" and p.get("type") in ("function_call_output", "custom_tool_call_output"):
            output = flat(p.get("output"))
            if REFUSED.search(output):
                yield "refused", "", "", p.get("call_id") or ""
            elif pane_opened(output):
                yield "opened", "", "", p.get("call_id") or ""


def spawns_check(command: str) -> bool:
    """A heredoc body or quoted text is no command. ponytail: a regex reading of shell, so a spawn inside `"$(…)"` reads as text and
    costs one rewrite. `<<\\EOF` is a heredoc too; it counts here alone, so what every other lead's check reads stays as it was."""
    return bool(SPAWN_CHECK.search(QUOTED.sub("q", HEREDOC.sub("", re.sub(r"(<<-?[ \t]*)\\", r"\1", command)))))


def chore(name: str, command: str) -> bool:
    commands = [c for c in COMMANDS.split(HEREDOC.sub("", command)) if re.search(r"\w", c)]
    return name in NOT_A_CHECK or all(CHORE_STEP.search(c.split("|")[0]) for c in commands)


def relay_reason(payload: dict, text: str, env) -> str | None:
    """The rewrite for the first last report after a pane's report arrived, when it shows no check of its own, or None.
    A report answered in a turn that has ended is not asked again. A pane that hands checks out shows a check pane instead: it
    opened one for a pane's report and is waiting, or it writes the `확인:` line from a check pane's report, which asks for no other."""
    path = payload.get("transcript_path")
    if not path or not LAST_REPORT.search(prose(text)):
        return None
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    for start in reversed([i for i, line in enumerate(lines) if "보고입니다(" in line]):  # parse only from the newest one on
        first = next(records(lines[start:start + 1]), None)
        relayed = RELAYED.search(unicodedata.normalize("NFC", first[1])) if first and first[0] == "in" else None
        if relayed:
            break
    else:
        return None
    after = list(records(lines[start + 1:]))
    reports = [i for i, (kind, said, _, _) in enumerate(after) if kind == "say" and LAST_REPORT.search(prose(said))]
    if reports and any(kind == "in" for kind, _, _, _ in after[reports[0] + 1:]):
        return None
    refused = {i for kind, _, _, i in after if kind == "refused"}
    stated = [v for v in (m.group(1).strip(" \t*_.,;") for m in CHECK_LINE.finditer(prose(text))) if v and not NONE.match(v)]
    if delegates_checks(payload, env):
        if relayed[1] == "확인":
            return None if any(BY_CHECK_PANE.search(v) for v in stated) else CHECK_REPORT_REASON
        opened = {i for kind, _, _, i in after if kind == "opened"}
        handed = any(kind == "call" and i in opened and spawns_check(command) for kind, command, _, i in after)
        return None if handed else DELEGATE_REASON
    ran = any(kind == "call" and i not in refused and not chore(name, command) for kind, command, name, i in after)
    return None if ran and stated else RELAY_REASON


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


def owed_reason(payload: dict, env) -> str | None:
    """The main pane's turn back for 요유's Slack words with no reply of ours after them, or None. The relay (main-channel.py)
    keeps the words it passed on in its state's `owed` ({thread: [words ts, first line]}) until it reads a reply; `post --thread`
    writes replied/<thread> at once, since the relay reads only every minute. Local files: no Slack read at a stop. Fails open."""
    if not env.get("HOME"):  # never the real state from a test
        return None
    try:
        peers = peers_module()
        folder = peers.state_dir(env)
        with open(os.path.join(folder, "channel.json"), encoding="utf-8") as f:
            owed = json.load(f).get("owed") or {}
        left = []
        for thread, (said, words) in sorted(owed.items()):
            try:
                with open(os.path.join(folder, "replied", os.path.basename(thread)), encoding="utf-8") as f:
                    answer = float(f.read().strip())
            except (OSError, ValueError):
                answer = 0.0
            if float(said) > answer:
                left.append(f"{thread}('{words}')")
        if not left:
            return None
        me = my_pane(herdr_list(env, "pane"), payload, env) or {}  # herdr only when something is owed
        if not me.get("terminal_id") or me["terminal_id"] != peers.main_terminal(env):
            return None
    except Exception:
        return None
    return OWED_REASON.format(", ".join(left))


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


def points_only(text: str) -> bool:
    """Some 요유 몫 item says where a question is instead of what it asks. A parenthesis that states it ends the match."""
    text = STRIP[2].sub(" ", STRIP[0].sub(" ", unicodedata.normalize("NFC", text)))
    for m in SHARE_FULL.finditer(text):
        for item in ITEM.split(m.group(1)):
            item = item.strip(" \t*_.,;")
            if POINTER_ITEM.match(item) or BARE_QUESTION.match(item):
                return True
    return False


def only_decisions(text: str) -> bool:
    """Nothing in 요유 몫 is a step 유요 could take: asking would only print the report again.

    Model text cannot stop that: told to end silently, Codex rewrote it 2 of 2 and Claude left a note 2 of 3 (2026-10-31).
    ponytail: word endings, so "할지 결정" also passes a step 유요 should just take. Replayed on the 102 fires of 10/28-10/31
    this drops 42 of 77 idle fires and 10 of 25 that did work, about 5 of them real wins. Ask once per session if those come back.
    """
    items = share_items(text)
    return bool(items) and all(is_decision(i) for i in items)


def share_items(text: str, aside: bool = False) -> list:
    """The items of every 요유 몫 value, each without its aside unless asked (its commas then split it, which comparing allows)."""
    text = STRIP[2].sub(" ", STRIP[0].sub(" ", unicodedata.normalize("NFC", text)))
    values = [ADDED.sub(r", \1", m.group(1)) for m in SHARE_FULL.finditer(text)]
    values = [v if aside else ASIDE.sub(" ", v) for v in values]
    return [i.strip() for v in values if not NONE.match(v.strip(" \t*_.,;")) for i in ITEM.split(v)
            if i.strip(" \t*_.,;") and not NONE.match(i.strip(" \t*_.,;"))]


def asked_before(payload: dict, text: str, env) -> bool:
    """This session was asked about every 요유 몫 item already; asking records the new ones. No session id or HOME: ask.

    요유 2026-11-05: the main pane kept one data deletion in 요유 몫 for hours. Asked at each report, it ended without a word and
    Claude Code ran one more turn for the empty reply. Items, not the whole value: replayed on that session, 42 asks fall to 26,
    27 with the whole value. Asides count: new work can sit in one ("PR 읽기(그리고 시험 실행)"); dropping them made it 23.
    ponytail: one small file per asked session; asking prunes those untouched for a week, so a session open longer is asked once more.
    Only files named as a session id go: the folder may hold anything else.
    """
    items = {" ".join(i.strip(" \t*_.,;").split()) for i in share_items(text, aside=True)}
    return bool(items) and not remember(payload, env, items)


def remember(payload: dict, env, keys: set) -> set:
    """The keys this session had not recorded, now recorded; all of them with no session id or HOME, or when the file fails."""
    session, home = os.path.basename(payload.get("session_id") or ""), env.get("HOME")
    if not (session and home and keys):
        return set(keys)
    folder = os.path.join(home, ".local", "state", "share-guard")
    try:
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, session), "a+", encoding="utf-8") as f:
            f.seek(0)
            new = set(keys) - set(f.read().splitlines())
            f.write("".join(f"{i}\n" for i in sorted(new)))
        for old in os.scandir(folder) if new else ():  # it asks anyway, so a failed prune changes nothing
            try:
                if RECORD.fullmatch(old.name) and old.stat().st_mtime < time.time() - 7 * 86400:
                    os.remove(old.path)
            except OSError:
                pass  # a folder, or a file gone already: the others still go
    except OSError:
        return set(keys)
    return new


def decide(payload: dict, env, claude: bool = False) -> str | None:
    if payload.get("stop_hook_active") or env.get("CLAUDE_CODE_ENTRYPOINT", "").startswith("sdk"):
        return None
    text = payload.get("last_assistant_message")
    if text is None and payload.get("transcript_path"):
        text = final_text(payload["transcript_path"])  # unreadable transcript raises: fail open
    if not isinstance(text, str):
        return None
    # The formats 요유 reads by. 요유 2026-11-04 ("너희끼리는 그냥 편하게 주고받아"): a pane under a lead writes to the lead as it likes.
    formats = [POINTER_REASON if points_only(text) else None, names_reason(text, env), WHAT_REASON if misses_what(text) else None,
               PROBLEM_REASON if misses_problem_line(text) else None, LONG_REASON if claude and not job_session(env) and too_long(text) else None]
    if any(formats) and under_lead(payload, env):  # herdr is asked only when a format would send the report back
        formats = []
    checks = (*formats[:4], left_open_reason(payload, text, env), relay_reason(payload, text, env), explain_reason(payload, text, env),
              stale_thread_reason(payload, text, env), owed_reason(payload, env), *formats[4:])
    rewrites = [r for r in checks if r]
    left = leaves_yoyoo_work(text)
    if left and not only_decisions(text) and not asked_before(payload, text, env):
        rewrites.append(DO_REASON if rewrites else REASON)  # a rewrite that is due must not be told to stay silent
    elif not left and asks_back(text):
        rewrites.append(DO_REASON if rewrites else ASK_REASON)
    return " ".join(rewrites) or None


def selftest():
    import tempfile

    os.environ.pop("HERDR_SOCKET_PATH", None)  # never the live herdr server of the pane running this
    denied = "권한 규칙이 거부한 명령은 형태를 바꿔 다시 실행하지 말고 그대로 보고한다"
    assert denied in DO_REASON and DO_REASON.index("다른 경로를 찾으세요") < DO_REASON.index(denied), \
        "the push to find another way must not cover a command a permission rule refused"
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
        # 요유 2026-11-04: told to watch a thread, the session asked whether to start the requests already in it
        "남은 것: 요유 몫: LaunchDaemon PR 작업을 이 칸에서 지금 시작할지 정하기(추천: 바로 시작) · 유요 몫: PR 두 개 · 세션: 닫으면 스레드 감시 멈춤",
        "남은 것: 요유 몫: B안 구현을 시작할지 결정 · 유요 몫: 없음",
        "남은 것: 요유 몫: 팀원에게 위 내용으로 답할지 정하기 · 유요 몫: 스레드 감시 중",  # 10/26: "감시한게 알아서하는거지?"
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
        "남은 것: 요유 몫: 두 안 중 정답 고르기 · 유요 몫: 없음",  # 답 alone is no step, only 답할지
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
            f.write(f'#!/bin/sh\necho "$HERDR_SOCKET_PATH" >> "{listing}.calls"\n'
                    f'case "$HERDR_SOCKET_PATH" in */sessions/workers/herdr.sock) s=-workers ;; *) s= ;; esac\n'
                    f'cat "{listing}$s.$1"\n')
        os.chmod(fake, 0o755)
        herdr_env = {**cli, "HERDR_BIN_PATH": fake, "HOME": d}  # HOME: never the real main-pane file
        done = "끝났습니다.\n\n남은 것: 요유 몫: 없음 · 유요 몫: 없음 · 세션: 닫아도 됨"
        me = {"pane_id": "w1:p1", "tab_id": "w1:t1", "terminal_id": "term_aa", "agent_session": {"value": "s1"}, "label": "🟢 일"}
        test_pane = {"pane_id": "w1:p2", "tab_id": "w1:t2", "terminal_id": "term_bb", "label": "🟢 🫡 임시 시험 Claude", "agent_status": "working"}

        def herdr_has_on(server, panes, agents=()):
            suffix = f"-{server}" if server else ""
            with open(f"{listing}{suffix}.pane", "w", encoding="utf-8") as f:
                json.dump({"result": {"panes": panes}}, f)
            with open(f"{listing}{suffix}.agent", "w", encoding="utf-8") as f:
                json.dump({"result": {"agents": list(agents)}}, f)

        def herdr_has(panes, agents=()):
            herdr_has_on("", panes, agents)

        def ends(text=done, fresh=True):  # fresh: this session has been told of no stopped pane yet
            if fresh:
                for name in os.listdir(os.path.join(d, ".local", "state", "share-guard")) if os.path.isdir(os.path.join(d, ".local", "state", "share-guard")) else ():
                    os.remove(os.path.join(d, ".local", "state", "share-guard", name))
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
        # 요유 2026-11-05: a pane the main pane kept open on purpose came back at each of its reports; 26 of its 66 such rewrites
        # named only panes it had been told of. A stop is told once a session; the same pane stopping again after work is a new stop.
        at = lambda seq, **more: [{"pane_id": "w1:p3", "name": "spawn-aa-1", "state_change_seq": seq}] + list(more.values())
        tidy = {**worker, "pane_id": "w1:p4", "terminal_id": "term_ff", "label": "🟢 🫡 정리"}
        herdr_has([me, worker], at(4))
        assert "구현(w1:p3)" in (ends() or "") and ends(fresh=False) is None, "the same stop is told once"
        herdr_has([me, worker, tidy], at(4, tidy={"pane_id": "w1:p4", "name": "spawn-aa-2", "state_change_seq": 1}))
        r = ends(fresh=False) or ""
        assert "정리(w1:p4)" in r and "구현(w1:p3)" not in r, f"a newly stopped pane is told alone: {r}"
        herdr_has([me, worker], at(6))
        assert "구현(w1:p3)" in (ends(fresh=False) or ""), "it worked and stopped again: a new stop"
        herdr_has([me, worker, test_pane], at(6, test={"pane_id": "w1:p2", "name": "spawn-aa-3"}))
        assert "임시 시험 Claude" in (ends(fresh=False) or ""), "an open test pane still comes back at every report"
        # The main pane opens its panes on the workers server: it reads that server too, each server's lists on their own.
        herdr_env["HOME"] = d
        os.makedirs(f"{d}/.local/state/orchestrator", exist_ok=True)
        open(f"{d}/.local/state/orchestrator/main", "w").write("term_aa")  # me is the main pane
        json.dump({"sessions": [{"name": "workers", "running": True, "socket_path": f"{d}/sessions/workers/herdr.sock"}]},
                  open(f"{listing}.session", "w"))
        same_id = {**worker, "pane_id": "w1:p3"}  # w1:p3 exists on both servers
        herdr_has([me, same_id], [{"pane_id": "w1:p3", "name": "spawn-aa-1"}])
        herdr_has_on("workers", [{**worker, "label": "🟢 🫡 workers 구현", "terminal_id": "term_ee"}], [{"pane_id": "w1:p3", "name": "spawn-aa-2"}])
        r = ends() or ""
        assert "구현(w1:p3)" in r and "workers 구현(workers/w1:p3)" in r, f"both stopped panes, each with its own server: {r}"
        assert "herdr --session workers pane close <ID>" in r, "a workers pane is closed on its own server"
        herdr_has_on("workers", [{**worker, "label": "🟢 🫡 workers 구현", "terminal_id": "term_ee"}], [{"pane_id": "w1:p3", "name": "spawn-cc-1"}])
        assert "workers/" not in (ends() or ""), "workers agent names decide workers panes, not the same id's name here"
        os.unlink(f"{d}/.local/state/orchestrator/main")
        # An ordinary lead's panes run there too (요유 2026-11-05): it hears the same.
        herdr_has_on("workers", [{**worker, "label": "🟢 🫡 workers 구현", "terminal_id": "term_ee"}], [{"pane_id": "w1:p3", "name": "spawn-aa-2"}])
        assert "workers 구현(workers/w1:p3)" in (ends() or ""), "a stopped pane on workers comes back to any lead that opened it"
        json.dump({"sessions": [{"name": "workers", "running": False, "socket_path": f"{d}/sessions/workers/herdr.sock"}]},
                  open(f"{listing}.session", "w"))
        assert "workers/" not in (ends() or ""), "workers stopped: only this server's panes"
        # 요유 2026-11-04 ("너희끼리는 그냥 편하게 주고받아"): a pane under a lead reports to that lead, so 요유's formats are not asked for.
        sub, spawned = {**me, "label": "🟢 🫡 설계"}, [{"pane_id": "w1:p1", "name": "spawn-zz-1"}]
        formats = ("변경했습니다.\n\n왜: 막혀서", "고쳤습니다.\n\n원인: 오타\n조치: 수정", "`w1:p9`에 넘겼습니다.", "상시 맥에서 돕니다.",
                   "끝.\n\n남은 것: 요유 몫: #12 질문에 답하기 · 유요 몫: 없음 · 세션: 닫아도 됨")
        wall = "가" * (LONG_REPLY + 1)
        herdr_has([sub], spawned)
        for t in formats:
            assert ends(t) is None, f"a pane under a lead writes as it likes: {t[:40]!r}"
        assert decide(p(wall, session_id="s1"), herdr_env, claude=True) is None, "nor is a long report sent to a page"
        assert REASON in (ends("남은 것: 요유 몫: Slack에 공지 올리기 · 유요 몫: 없음") or ""), "it still does what it can itself"
        herdr_has([sub, worker], [*spawned, *opened])
        assert "구현(w1:p3)" in (ends() or ""), "and still finishes the panes it opened"
        for label, panes, agents in (("a lead", [{**sub, "label": "🟢 👑 설계"}], spawned), ("a pane 요유 opened", [me], []),
                                     ("another pane's spawn name", [sub], [{"pane_id": "w1:p7", "name": "spawn-zz-1"}])):
            herdr_has(panes, agents)
            assert all(ends(t) for t in formats), f"{label} still reports to 요유"
            assert decide(p(wall, session_id="s1"), herdr_env, claude=True) == LONG_REASON, f"{label}: long goes to a page"
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
    # 2026-11-05: "없음, 시험 실행" passed as empty. A comma right after 없음 lists work; one inside the reason after "없음." does not.
    assert decide(p("남은 것: 요유 몫: 없음, 시험 실행 · 유요 몫: 없음 · 세션: 닫아도 됨"), cli) == REASON, "work after '없음,' is asked"
    assert decide(p("남은 것: 요유 몫: 없음, 두 안 중 하나 고르기 · 유요 몫: 없음"), cli) is None, "its 없음 is no work beside a decision"
    assert not leaves_yoyoo_work("남은 것: 요유 몫: 없음. 보고 싶으면 말씀해 주세요, 한 번 보내 드립니다 · 세션: 닫아도 됨"), "a reason's comma"
    # 2026-11-05: work added in an aside hid there. "없음(새 작업: …)" passed as empty and "설계 승인(그리고 …)" as a decision.
    for v in ("없음(새 작업: 시험 실행)", "설계 승인(그리고 시험 실행)", "없음（그리고 PR 읽기）"):
        assert decide(p(f"남은 것: 요유 몫: {v} · 유요 몫: 없음 · 세션: 닫아도 됨"), cli) == REASON, f"work in an aside is asked: {v}"
    for v in ("없음(보드가 감시)", "없음(새 작업 없음)", "설계 승인(추천: A안, 그리고 B안은 버림)"):
        assert decide(p(f"남은 것: 요유 몫: {v} · 유요 몫: 없음 · 세션: 닫아도 됨"), cli) is None, f"a reason is no work: {v}"
    # 요유 2026-11-04 (api#993): a 요유 몫 that only points at the board made them read the issue history on GitHub.
    for t in ("남은 것: 요유 몫: https://github.com/munice-dev/nightly_api/issues/993 의 질문에 답하기 · 유요 몫: 없음",
              "남은 것: 요유 몫: #993 질문에 답하기 · 유요 몫: 없음",
              "남은 것: 요유 몫: 보드 질문에 답하기 · 유요 몫: 없음 · 세션: 닫아도 됨",
              "남은 것: 요유 몫: api#993 댓글의 결정 · 유요 몫: 없음",
              "남은 것: 요유 몫: 보드 항목의 선택지 고르기 · 유요 몫: 없음",
              "남은 것: 요유 몫: 위 질문에 답하기 · 유요 몫: 없음",
              "남은 것: 요유 몫: 질문에 답하기, 테스트할 사람 정하기 · 유요 몫: 없음"):
        assert (decide(p(t), cli) or "").startswith(POINTER_REASON), f"a pointer is not a decision: {t[:60]!r}"
    for t in ("남은 것: 요유 몫: #972 제목을 바꿔 쌓인 PR 5개를 staging에 올릴지 결정(추천: 바꾼다) · 유요 몫: 없음",
              "남은 것: 요유 몫: https://github.com/x/y/issues/1 의 질문(#972 제목을 바꿔도 되는지)에 답하기 · 유요 몫: 없음",
              "남은 것: 요유 몫: 세 길 중 하나 고르기 · 유요 몫: 없음",
              "남은 것: 요유 몫: 훅을 만들지 결정 · 유요 몫: 없음",
              "요유 몫은 보드 질문을 가리키기만 하면 안 됩니다."):
        assert decide(p(t), cli) is None, f"the decision is stated: {t[:60]!r}"

    # 요유 2026-11-05: the main pane carried one data deletion in 요유 몫 report after report. Asked each time, it ended without a word,
    # and Claude Code ran another turn for the empty reply, more than six times. Each 요유 몫 item is asked about once a session.
    with tempfile.TemporaryDirectory() as d:
        home = {**cli, "HOME": d}
        held = ("남은 것: 요유 몫: 데이터셋을 지울지 정해 주세요. 추천은 메인 칸에 `prod 허용`을 쳐서 읽기 기록을 먼저 보고, "
                "아무도 읽지 않았으면 지우는 것입니다 · 유요 몫: 없음 · 세션: 닫아도 됨")
        linked = held.replace("것입니다", "것입니다(https://example.com/list?record=1)")
        assert decide(p(held, session_id="s1"), home) == REASON, "asked the first time"
        assert decide(p(held, session_id="s1"), home) is None, "the same 요유 몫 is not asked again"
        assert decide(p("다시 정리했습니다.\n\n" + linked, session_id="s1"), home) == REASON, "a new aside is asked: it may hold new work"
        assert decide(p(f"왜: 막혀서\n{held}", session_id="s1"), home) == WHAT_REASON, "a due rewrite does not bring it back"
        assert decide(p(held.replace("없음 · 세션", "PR 대기 · 세션"), session_id="s1"), home) is None, "유요 몫 is not part of it"
        assert decide(p(held.replace("데이터셋을", "값 넣기, 데이터셋을"), session_id="s1"), home) == REASON, "a new item is asked"
        uid = "{:08x}-0000-4000-8000-000000000000".format  # Claude's and Codex's session ids
        assert decide(p(held, session_id=uid(2)), home) == REASON, "another session is asked"
        for first, then in (("PR 읽기", "PR 읽기(그리고 테스트 실행)"), ("시험하기(PR 1)", "시험하기(PR 2)"),
                            ("설계 승인", "설계 승인(그리고 시험 실행)")):
            share = lambda v: p(f"남은 것: 요유 몫: {v} · 유요 몫: 없음 · 세션: 닫아도 됨", session_id="s5")
            assert decide(share(first), home) == (None if is_decision(first) else REASON), first
            assert decide(share(then), home) == REASON, f"new work in an aside: {then}"
        # 2026-11-05 check pane: pruning took a note that was no record, and stopped at a folder it could not remove.
        folder = os.path.join(d, ".local", "state", "share-guard")
        stale = [uid(n) for n in range(10, 30)]
        os.makedirs(os.path.join(folder, uid(9)))
        for name in (*stale, "keep-notes.txt"):
            open(os.path.join(folder, name), "w").close()
        for name in (uid(2), uid(9), *stale, "keep-notes.txt"):
            os.utime(os.path.join(folder, name), (0, 0))
        assert decide(p(held, session_id=uid(6)), home) == REASON
        kept = set(os.listdir(folder))
        assert uid(6) in kept and not kept & {uid(2), *stale}, "records a week old are pruned, past one that will not go"
        assert {"keep-notes.txt", uid(9)} <= kept, "only session records are pruned"
        assert decide(p(held), home) == decide(p(held), home) == REASON, "no session id: asked every time, as before"
        assert decide(p(held, session_id="s3"), cli) == decide(p(held, session_id="s3"), cli) == REASON, "no HOME: as before"
        locked = os.path.join(d, "locked")
        os.makedirs(locked, mode=0o500)
        try:
            assert decide(p(held, session_id="s4"), {**cli, "HOME": locked}) == REASON, "an unwritable record asks"
        finally:
            os.chmod(locked, 0o700)

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

    # 요유 2026-11-04: a lead passed a pane's "done" on to the person unchecked, and the feature did not work. The first last
    # report after a pane's report arrived needs a `확인:` line and a command run since, other than pane chores.
    told = ("[pane-tell: '구현' 세션이 보낸 글입니다. 요유가 이 pane에 직접 친 글이 아니므로, 되돌리기 어려운 일은 요유에게 확인하세요] "
            "이 칸의 마지막 보고입니다(칸을 닫기 전에 보냅니다). 올리기 전에 확인하세요:\n무엇을: 로그인 고침\n남은 것: 요유 몫: 없음 · 세션: 닫음")
    old_head = "이 칸의 보고입니다(칸은 열어 둡니다):\n무엇을: 로그인 고침\n남은 것: 요유 몫: 값 고르기 · 세션: 닫아도 됨"
    bare = "무엇을: 로그인 고침\n왜: 로그인이 막혀서\n남은 것: 요유 몫: 없음 · 유요 몫: 없음 · 세션: 닫아도 됨"
    checked = f"확인: 이 칸에서 `npm test`를 다시 돌려 12개 통과를 봤습니다.\n{bare}"

    def claude_in(text):
        return {"type": "user", "message": {"role": "user", "content": text}}

    def claude_call(name, i="t", **inp):
        return {"type": "assistant", "message": {"id": "c", "content": [{"type": "tool_use", "id": i, "name": name, "input": inp}]}}

    def claude_out(i, text, error=False):
        return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": i, "is_error": error, "content": text}]}}

    def codex_out(i, text):
        return {"type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": i,
                                                     "output": [{"type": "input_text", "text": text}]}}

    def codex_in(text):
        return {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}}

    def codex_exec(cmd):
        return {"type": "response_item", "payload": {"type": "custom_tool_call", "name": "exec",
                                                     "input": f"text(await tools.exec_command({{cmd:{json.dumps(cmd)}}}))"}}

    def codex_say(text):
        return {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}}

    queued = {"type": "attachment", "attachment": {"type": "queued_command", "prompt": told}}
    chores = [claude_call("Bash", command="herdr pane close w1:p3"), claude_call("Bash", command="pane-label '일 정리'"),
              claude_call("Read", file_path="/Users/yoyoo/.local/state/pane-reports/2026-11-04.md"),
              claude_call("Bash", command="cd /w && cat ~/.local/state/pane-reports/2026-11-04.md"), claude_call("TaskUpdate", taskId="1")]
    test_run = claude_call("Bash", command="cd /w && npm test && herdr pane close w1:p3")

    def relayed(records, text):
        path = transcript(records)
        try:
            return decide(p(text, transcript_path=path), cli)
        finally:
            os.unlink(path)

    for label, records, text in (
            ("no check run", [claude_in(told)], bare),
            ("only pane chores since", [claude_in(told), *chores], checked),
            ("a check run but no 확인 line", [claude_in(told), test_run], bare),
            ("a 확인 line that says none", [claude_in(told), test_run], bare + "\n확인: 없음"),
            ("the old head", [claude_in(old_head)], bare),
            ("queued into a running turn", [claude_in("다른 일 해 줘"), test_run, queued], bare),
            ("the reply itself is in the transcript", [claude_in(told), said("m9", bare)], bare),
            ("Codex: no check run", [codex_in(told)], bare),
            ("Codex: only pane chores", [codex_in(told), codex_exec("herdr pane close w1:p3; pane-label x")], checked),
            # Codex review, 2026-11-04: commands in other shapes, a check handed to another agent, a call a hook refused.
            ("Codex: a chore as JSON arguments", [codex_in(told), {"type": "response_item", "payload": {
                "type": "function_call", "name": "exec_command", "arguments": json.dumps({"cmd": "herdr pane close w1:p3"})}}], checked),
            ("Codex: a chore in single quotes", [codex_in(told), {"type": "response_item", "payload": {
                "type": "custom_tool_call", "name": "exec", "input": "text(await tools.exec_command({cmd:'herdr pane close w1:p3'}))"}}], checked),
            ("the check handed to another agent", [claude_in(told), claude_call("Agent", prompt="결과 확인해 줘")], checked),
            ("Codex: the check handed to another agent", [codex_in(told), {"type": "response_item", "payload": {
                "type": "function_call", "name": "spawn_agent", "arguments": "{}"}}], checked),
            ("a check a hook refused", [claude_in(told), claude_call("Bash", "r1", command="npm test"),
                                        claude_out("r1", "PreToolUse:Bash hook error: secret-print-guard: 막음", error=True)], checked),
            ("Codex: a check a hook refused", [codex_in(told), {**codex_exec("npm test"), "payload": {**codex_exec("npm test")["payload"], "call_id": "c1"}},
                                               codex_out("c1", "Script error:\nCommand blocked by PreToolUse hook: secret-print-guard")], checked),
            # Refutation review, 2026-11-04: a pipe, a heredoc body or an assignment made a pane chore count as a check.
            ("a chore piped into head", [claude_in(told), claude_call("Bash", command="herdr pane close w5:pPP 2>&1 | head -3")], checked),
            ("a chore inside an assignment", [claude_in(told), claude_call("Bash", command="sid=$(herdr pane get w1:p3 | jq -r .a); echo $sid")], checked),
            ("an assignment and a pane-report read", [claude_in(told), claude_call("Bash", command="f=/x/pane-reports/a.md\ntail -20 ~/.local/state/pane-reports/a.md")], checked),
            ("a heredoc body is no command", [claude_in(told), claude_call("Bash", command="pane-tell w1:p3 \"$(cat <<'EOF'\nnpm test 돌려 줘\nEOF\n)\"")], checked)):
        assert "하위 칸" in (relayed(records, text) or ""), f"should send back: {label}"
    for label, records, text in (
            ("checked and says so", [claude_in(told), *chores, test_run], checked),
            ("queued, then checked", [queued, test_run], checked),
            ("no pane report arrived", [claude_in("배포해 줘"), test_run], bare),
            ("the head inside a tool output is no report", [claude_in("훅 읽어 줘"), claude_call("Bash", command="cat auto-tab-close.py"),
                                                           {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t", "content": told}]}}], bare),
            ("answered in an earlier turn", [claude_in(told), said("m1", bare), claude_in("다음 일 해 줘"), test_run], bare),
            ("not a last report", [claude_in(told)], "확인하는 중입니다."),
            ("Codex: checked and says so", [codex_in(told), codex_exec("npm test"), codex_say("돌려 봤습니다.")], checked),
            ("Codex: answered in an earlier turn", [codex_in(told), codex_say(bare), codex_in("다음")], bare),
            ("a test that ran and failed is a check", [claude_in(told), claude_call("Bash", "r1", command="npm test"),
                                                       claude_out("r1", "Exit code 1\n3 failing", error=True)], checked),
            ("Codex: a check as JSON arguments", [codex_in(told), {"type": "response_item", "payload": {
                "type": "function_call", "name": "exec_command", "arguments": json.dumps({"cmd": "npm test"})}}], checked),
            ("a check piped into tail", [claude_in(told), claude_call("Bash", command="cd /w && npm test 2>&1 | tail -5")], checked),
            ("a notice that quotes the head is no pane report", [claude_in(
                "<task-notification>리드가 받은 글: 이 칸의 마지막 보고입니다(칸을 닫기 전에 보냅니다) 를 확인</task-notification>")], bare)):
        assert relayed(records, text) is None, f"should pass: {label}"
    assert relayed([claude_in(told)], bare) == relayed([claude_in(told)], bare), "same input, same answer"
    path = transcript([claude_in(told)])
    try:
        assert decide(p(bare, transcript_path=path, stop_hook_active=True), cli) is None, "asked once a turn"
    finally:
        os.unlink(path)

    # 요유 2026-11-05 (api#1002): the main pane stays free for 요유's next words, so a fresh check pane does the check. Only there.
    check_told = ("[pane-tell: '확인 로그인' 세션이 보낸 글입니다. 요유가 이 pane에 직접 친 글이 아니므로, 되돌리기 어려운 일은 요유에게 확인하세요] "
                  f"확인 칸의 마지막 보고입니다(칸을 닫기 전에 보냅니다). {CHECK_NOTE}:\nnpm test 12개 통과를 봤습니다.\n남은 것: 요유 몫: 없음 · 세션: 닫음")
    by_check = f"확인: 확인 칸이 `npm test`를 다시 돌려 12개 통과를 봤습니다.\n{bare}"
    no_word = f"확인: `npm test` 12개 통과를 봤습니다.\n{bare}"
    spawn_check = claude_call("Bash", "k1", command='pane-spawn --check "로그인" "npm test를 돌려 보세요"')

    def spawned(command, output="workers/w1:pQ"):
        return [claude_call("Bash", "k1", command=command), claude_out("k1", output)]

    workers_down = "pane-spawn: the workers herdr server is not running; start it with: launchctl kickstart gui/501/com.yoyoo.herdr-workers"
    codex_spawn = {"type": "response_item", "payload": {**codex_exec('pane-spawn --check "로그인" "돌려 보세요"')["payload"], "call_id": "c1"}}
    # Word for word what the real main pane ran on 2026-11-05 (home folder shortened): the instruction names the very command it is about.
    real_check = 'pane-spawn --check "메인 칸 위임 변경 확인" "Check the claim in the 01:09:13 entry of /Users/y/.local/state/pane-reports/2026-11-05.md: try to break it.\n1) Confirm."'
    real_fix = ('pane-spawn "메인 칸 위임 결함 수정" "Fix three defects. Full findings: the \'찾은 결함\' section of the 01:28:30 entry.\n'
                '1. A failed pane-spawn --check (exit 1 with workers down, exit 2) still counts as handing the check over.\npane-spawn --check a b\n; pane-spawn --check c d"')
    with tempfile.TemporaryDirectory() as d:
        fake = os.path.join(d, "herdr")
        with open(fake, "w", encoding="utf-8") as f:
            f.write('#!/bin/sh\necho \'{"result":{"panes":[{"pane_id":"w1:p1","terminal_id":"term_M",'
                    '"agent_session":{"value":"s1"},"label":"🟢 👑 메인 오케스트레이터"}]}}\'\n')
        os.chmod(fake, 0o755)
        here = {**cli, "HERDR_BIN_PATH": fake, "HOME": d}
        state = os.path.join(d, ".local", "state", "orchestrator", "main")
        os.makedirs(os.path.dirname(state))

        def own_terminal(value):
            with open(state, "w", encoding="utf-8") as f:
                f.write(value)

        def in_pane(records, text):
            path = transcript(records)
            try:
                return decide(p(text, transcript_path=path, session_id="s1"), here)
            finally:
                os.unlink(path)

        own_terminal("term_M\n")
        for label, records, text, want in (
                ("a report with no 확인 line and no check pane", [claude_in(told)], bare, DELEGATE_REASON),
                ("a claimed check with no check pane behind it", [claude_in(told)], by_check, DELEGATE_REASON),
                ("a check it ran itself", [claude_in(told), test_run], checked, DELEGATE_REASON),
                ("a pane that is no check pane", [claude_in(told), *spawned('pane-spawn "로그인" "돌려 보세요"')], bare, DELEGATE_REASON),
                ("a word in another command", [claude_in(told), *spawned('rg "pane-spawn --check" tools')], bare, DELEGATE_REASON),
                ("a check pane a hook refused", [claude_in(told), spawn_check, claude_out("k1", "PreToolUse:Bash hook error: 막음", error=True)], bare, DELEGATE_REASON),
                # A check pane that never opened hands nothing over, however the command ended (a check pane's finding, 2026-11-05).
                ("a spawn with workers down, exit 1", [claude_in(told), spawn_check, claude_out("k1", f"Exit code 1\n{workers_down}", error=True)], bare, DELEGATE_REASON),
                ("a spawn refused as bad usage, exit 2", [claude_in(told), spawn_check, claude_out("k1", "Exit code 2\npane-spawn: --check is for the main pane", error=True)], bare, DELEGATE_REASON),
                ("a spawn whose pane shows no work, exit 1", [claude_in(told), spawn_check, claude_out("k1", "Exit code 1\nworkers/w1:pQ\npane-spawn: workers/w1:pQ shows no work", error=True)], bare, DELEGATE_REASON),
                ("a failed spawn behind `; echo`, which exits 0", [claude_in(told), claude_call("Bash", "k1", command='pane-spawn --check "a" "b"; echo done'),
                                                                   claude_out("k1", f"{workers_down}\ndone")], bare, DELEGATE_REASON),
                ("a failed spawn behind `| tail`, which exits 0", [claude_in(told), claude_call("Bash", "k1", command='pane-spawn --check "a" "b" 2>&1 | tail -3'),
                                                                   claude_out("k1", workers_down)], bare, DELEGATE_REASON),
                ("Codex: a spawn with workers down, exit 1", [codex_in(told), codex_spawn, codex_out("c1", json.dumps({"exit_code": 1, "output": workers_down}))], bare, DELEGATE_REASON),
                ("Codex: a spawn refused, exit 2", [codex_in(told), codex_spawn, codex_out("c1", json.dumps({"exit_code": 2, "output": "pane-spawn: usage"}))], bare, DELEGATE_REASON),
                ("Codex: a failed spawn behind `; echo`, exit 0", [codex_in(told), codex_spawn, codex_out("c1", json.dumps({"exit_code": 0, "output": f"{workers_down}\ndone"}))], bare, DELEGATE_REASON),
                # No error line of pane-spawn's own: it never ran.
                ("a cd that failed before the spawn", [claude_in(told), claude_call("Bash", "k1", command='cd /nope && pane-spawn --check "a" "b"'),
                                                       claude_out("k1", "Exit code 1\ncd: no such file or directory: /nope", error=True)], bare, DELEGATE_REASON),
                ("Codex: a spawn it could not find, exit 127", [codex_in(told), codex_spawn, codex_out("c1", json.dumps({"exit_code": 127, "output": "zsh:1: command not found: pane-spawn"}))], bare, DELEGATE_REASON),
                # Words about the command are not the command.
                ("double-quoted text", [claude_in(told), *spawned('echo "a; pane-spawn --check b"')], bare, DELEGATE_REASON),
                ("single-quoted text", [claude_in(told), *spawned("echo 'a; pane-spawn --check b'")], bare, DELEGATE_REASON),
                ("escaped quotes in double-quoted text", [claude_in(told), *spawned(r'echo "say \"x; pane-spawn --check y\" now"')], bare, DELEGATE_REASON),
                ("a heredoc body", [claude_in(told), *spawned("cat <<EOF\npane-spawn --check a b\nEOF")], bare, DELEGATE_REASON),
                ("a heredoc body with a lone quote", [claude_in(told), *spawned("cat <<'EOF'\nit's\npane-spawn --check a b\nEOF")], bare, DELEGATE_REASON),
                ("a heredoc body under <<\\EOF", [claude_in(told), *spawned("cat <<\\EOF\npane-spawn --check a b\nEOF")], bare, DELEGATE_REASON),
                ("a heredoc body under <<-\\EOF", [claude_in(told), *spawned("cat <<-\\EOF\n\tpane-spawn --check a b\n\tEOF")], bare, DELEGATE_REASON),
                # An address beside pane-spawn's own error line proves nothing (a check pane's seven rows, 2026-11-05).
                ("a later step prints bare pane ids", [claude_in(told), *spawned('pane-spawn --check "a" "b"; herdr agent list | jq', f"{workers_down}\nw1:p2\nw1:p3")], bare, DELEGATE_REASON),
                ("an earlier step prints a bare pane id", [claude_in(told), *spawned('herdr agent list | jq; pane-spawn --check "a" "b"', f"w1:p2\n{workers_down}")], bare, DELEGATE_REASON),
                ("an echo of a known pane after it", [claude_in(told), *spawned('pane-spawn --check "a" "b"; echo workers/w1:pQ', f"{workers_down}\nworkers/w1:pQ")], bare, DELEGATE_REASON),
                ("a plain spawn opened, then --check failed", [claude_in(told), *spawned('pane-spawn "x" "y"; pane-spawn --check "a" "b"', f"workers/w1:pQ\n{workers_down}")], bare, DELEGATE_REASON),
                ("--check failed, then a plain spawn opened", [claude_in(told), *spawned('pane-spawn --check "a" "b"; pane-spawn "x" "y"', f"{workers_down}\nworkers/w1:pQ")], bare, DELEGATE_REASON),
                ("shows no work, then a bare pane id", [claude_in(told), *spawned('pane-spawn --check "a" "b"; herdr agent list | jq', "workers/w1:pQ\npane-spawn: workers/w1:pQ shows no work\nw1:p2")], bare, DELEGATE_REASON),
                ("Codex: a later step prints a bare pane id", [codex_in(told), codex_spawn, codex_out("c1", json.dumps({"exit_code": 0, "output": f"{workers_down}\nw1:p2"}))], bare, DELEGATE_REASON),
                # Only a printed address counts: no address, no check pane we can show.
                ("a spawn whose output was thrown away", [claude_in(told), *spawned('pane-spawn --check "a" "b" 2>/dev/null; echo done', "done")], bare, DELEGATE_REASON),
                ("a spawn with no output recorded", [claude_in(told), spawn_check], bare, DELEGATE_REASON),
                ("Codex: a spawn with no address, exit 0", [codex_in(told), codex_spawn, codex_out("c1", json.dumps({"exit_code": 0, "output": "done"}))], bare, DELEGATE_REASON),
                # What lets a later step's failure through must not let a spawn that never opened through (below).
                ("a failed spawn and a later step that failed", [claude_in(told), claude_call("Bash", "k1", command='pane-spawn --check "a" "b"; npm test'),
                                                                 claude_out("k1", f"Exit code 1\n{workers_down}\n3 failing", error=True)], bare, DELEGATE_REASON),
                ("Codex: a spawn whose pane shows no work", [codex_in(told), codex_spawn, codex_out("c1", json.dumps({"exit_code": 1, "output": "workers/w1:pQ\npane-spawn: workers/w1:pQ shows no work"}))], bare, DELEGATE_REASON),
                ("a failed spawn and a pane id in a later step's JSON", [claude_in(told), claude_call("Bash", "k1", command='pane-spawn --check "a" "b"; herdr agent list'),
                                                                         claude_out("k1", f'{workers_down}\n{{"pane_id":"w1:p2"}}')], bare, DELEGATE_REASON),
                ("Codex: a failed spawn and a pane id in a later step's JSON", [codex_in(told), codex_spawn, codex_out("c1", json.dumps({"exit_code": 0, "output": f'{workers_down}\n{{"pane_id":"w1:p2"}}'}))], bare, DELEGATE_REASON),
                ("a plain pane whose instruction names the check", [claude_in(told), *spawned(real_fix)], bare, DELEGATE_REASON),
                ("a check pane's report and no 확인 line", [claude_in(check_told)], bare, CHECK_REPORT_REASON),
                ("a check pane's report and 확인: 없음", [claude_in(check_told)], bare + "\n확인: 없음", CHECK_REPORT_REASON),
                ("a 확인 line that does not say a check pane did it", [claude_in(check_told)], no_word, CHECK_REPORT_REASON),
                ("a later pane's report still needs its own check", [claude_in(check_told), said("m1", "읽었습니다."), claude_in(told)], by_check, DELEGATE_REASON)):
            assert in_pane(records, text) == want, f"main pane should send back: {label}"
        for label, records, text in (
                ("its options in any order", [claude_in(told), *spawned('pane-spawn --cwd /w/api --check "a" "b"')], bare),
                ("after a cd", [claude_in(told), *spawned('cd /w && pane-spawn --check "a" "b"')], bare),
                ("waiting on the check pane it opened: a spawn that printed its pane", [claude_in(told), spawn_check, claude_out("k1", "workers/w1:pQ")], bare),
                ("a pane with no server in its address", [claude_in(told), spawn_check, claude_out("k1", "w1:pQ")], bare),
                ("a failed spawn and a retry that opened", [claude_in(told), spawn_check, claude_out("k1", f"Exit code 1\n{workers_down}", error=True),
                                                            claude_call("Bash", "k2", command='pane-spawn --check "a" "b"'), claude_out("k2", "workers/w1:pQ")], bare),
                ("another command that failed after the spawn", [claude_in(told), spawn_check, claude_out("k1", "workers/w1:pQ"),
                                                                 claude_call("Bash", "k2", command="rg x /nope"), claude_out("k2", "Exit code 2", error=True)], bare),
                ("Codex: a spawn that opened, exit 0", [codex_in(told), codex_spawn, codex_out("c1", json.dumps({"exit_code": 0, "output": "workers/w1:pQ"}))], bare),
                # The pane opened; a later step of the same command took nothing back (a check pane's finding, 2026-11-05).
                ("opened, a later step failed", [claude_in(told), claude_call("Bash", "k1", command='pane-spawn --check "a" "b"; npm test'),
                                                 claude_out("k1", "Exit code 1\nworkers/w1:pQ\n3 failing", error=True)], bare),
                ("opened, a later step printed its error words", [claude_in(told), claude_call("Bash", "k1", command='pane-spawn --check "a" "b"; tail -3 ~/.local/state/pane-reports/a.md'),
                                                                  claude_out("k1", f"workers/w1:pQ\n실패: {workers_down}")], bare),
                ("opened, a later step printed an exit code", [claude_in(told), claude_call("Bash", "k1", command='pane-spawn --check "a" "b"; cat r.json'),
                                                               claude_out("k1", 'workers/w1:pQ\n{"exit_code": 1}')], bare),
                ("Codex: opened, a later step failed", [codex_in(told), codex_spawn, codex_out("c1", json.dumps({"exit_code": 1, "output": "workers/w1:pQ\n3 failing"}))], bare),
                ("Codex: opened, as its exec tool writes it", [codex_in(told), codex_spawn, codex_out("c1", 'Script completed\nOutput:\n{"chunk_id":"a","exit_code":1,"output":"workers/w1:pQ\\n3 failing"}')], bare),
                ("Codex: opened after an earlier step's output", [codex_in(told), codex_spawn, codex_out("c1", json.dumps({"exit_code": 1, "output": "hi\nworkers/w1:pQ\n3 failing"}))], bare),
                ("a leading env assignment", [claude_in(told), *spawned('FOO=1 pane-spawn --check "a" "b"')], bare),
                ("a quoted env assignment", [claude_in(told), *spawned('FOO="a b" BAR=1 /w/pane-spawn --check "a" "b"')], bare),
                ("an output kept in a variable", [claude_in(told), *spawned('out=$(pane-spawn --check "a" "b"); echo "$out"')], bare),
                ("a quoted option value before it", [claude_in(told), *spawned('pane-spawn --cwd "/w/my repo" --check "a" "b"')], bare),
                ("an instruction from a heredoc", [claude_in(told), *spawned("pane-spawn --check \"a\" \"$(cat <<'EOF'\nit's a; pane-spawn x\nEOF\n)\"")], bare),
                ("the real main pane's command", [claude_in(told), *spawned(real_check)], bare),
                ("what the check pane ran and saw", [claude_in(check_told)], by_check),
                ("no command of its own after a check pane's report", [claude_in(told), spawn_check, said("m1", "맡겼습니다."), claude_in(check_told)], by_check),
                ("answered in an earlier turn", [claude_in(check_told), said("m1", bare), claude_in("다음")], bare),
                ("no pane report arrived", [claude_in("배포해 줘")], bare),
                ("not a last report", [claude_in(told)], "확인 칸을 열겠습니다.")):
            assert in_pane(records, text) is None, f"main pane should pass: {label}"
        assert CHECK_REPORT_REASON.count("pane-spawn") == 0 and "또 열지" in CHECK_REPORT_REASON, "no check of a check: the reason opens nothing"
        assert "pane-spawn --check" in DELEGATE_REASON, "the reason says what to run"
        # Every other lead, and a main-pane file that is missing or names another terminal: the rule as it was, word for word.
        for label, setup in (("names another terminal", lambda: own_terminal("term_gone\n")), ("empty", lambda: own_terminal("")),
                             ("missing", lambda: os.unlink(state))):
            setup()
            assert in_pane([claude_in(told)], bare) == RELAY_REASON, f"state file {label}: no check is sent back as ever"
            assert in_pane([claude_in(told), test_run], checked) is None, f"state file {label}: a lead's own check passes"
            assert in_pane([claude_in(told), spawn_check], bare) == RELAY_REASON, f"state file {label}: a check pane answers for nothing"
            assert in_pane([claude_in(check_told)], by_check) == RELAY_REASON, f"state file {label}: a check pane's report is no check"
            assert in_pane([claude_in(check_told), test_run], checked) is None, f"state file {label}: its own check passes"
            assert in_pane([claude_in(told), claude_call("Bash", "r1", command="npm test"), claude_out("r1", "Exit code 1\n3 failing", error=True)], checked) is None, \
                f"state file {label}: a test that ran and failed is still a check"
            assert in_pane([claude_in(told), spawn_check, claude_out("k1", f"Exit code 1\n{workers_down}", error=True)], bare) == RELAY_REASON, \
                f"state file {label}: a failed spawn is no check either"
        own_terminal("term_M\n")
        with open(fake, "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\nexit 1\n")
        assert in_pane([claude_in(told)], bare) == RELAY_REASON, "herdr down: the ordinary rule"
        assert in_pane([claude_in(told), test_run], checked) is None, "herdr down: a lead's own check passes"

    # Lead 2026-11-05 (tf#1083's '머지 ㄱㄱ'): the main pane handed the work to a pane and left 요유's Slack words unanswered.
    with tempfile.TemporaryDirectory() as d:
        fake, listing, state = os.path.join(d, "herdr"), os.path.join(d, "panes"), os.path.join(d, ".local", "state", "orchestrator")
        with open(fake, "w", encoding="utf-8") as f:
            f.write(f'#!/bin/sh\ncat "{listing}.$1"\n')
        os.chmod(fake, 0o755)
        main = {"pane_id": "w1:p1", "terminal_id": "term_M", "agent_session": {"value": "s1"}, "label": "🟢 👑 메인 오케스트레이터"}
        json.dump({"result": {"panes": [main]}}, open(f"{listing}.pane", "w"))
        json.dump({"result": {"agents": []}}, open(f"{listing}.agent", "w"))
        os.makedirs(os.path.join(state, "replied"))
        open(os.path.join(state, "main"), "w").write("term_M\n")
        env, thread = {**cli, "HERDR_BIN_PATH": fake, "HOME": d}, "1793870239.004549"

        def owes(**owed):
            json.dump({"cursor": "1793871000.000000", "owed": owed}, open(os.path.join(state, "channel.json"), "w"))

        def replied(ts):
            open(os.path.join(state, "replied", thread), "w").write(ts + "\n")

        def stops(text="확인했습니다.", **kw):
            return decide(p(text, session_id="s1", **kw), env)

        owes(**{thread: ["1793871000.000100", "머지 ㄱㄱ"]})
        r = stops() or ""
        assert f"{thread}('머지 ㄱㄱ')" in r and "post --thread" in r and "done --thread" in r, f"owed words come back: {r}"
        assert stops(stop_hook_active=True) is None, "once a turn"
        replied("1793870900.000000")
        assert thread in (stops() or ""), "a reply from before the words answers nothing"
        replied("1793871100.000000")
        assert stops() is None, "post --thread wrote its reply: the relay's minute does not matter"
        owes(**{thread: ["1793871200.000100", "그리고 이것도"], "1793861963.607349": ["1793861963.607349", "토큰 이슈"]})
        r = stops() or ""
        assert "그리고 이것도" in r and "토큰 이슈" in r, f"newer words after the reply, and a thread never answered: {r}"
        assert stops(f"남은 것: 요유 몫: 없음 · 유요 몫: 없음 · 세션: 닫아도 됨") == r, "whatever the reply says"
        open(os.path.join(state, "main"), "w").write("term_X\n")
        assert stops() is None, "only the main pane: another pane is not who answers 요유's channel"
        open(os.path.join(state, "main"), "w").write("term_M\n")
        assert stops() is not None
        assert decide(p("확인했습니다.", session_id="s1"), {**env, "HOME": ""}) is None, "no HOME: never the real state"
        owes()
        assert stops() is None, "nothing owed"
        open(os.path.join(state, "channel.json"), "w").write("{broken")
        assert stops() is None, "an unreadable state passes"
        os.unlink(os.path.join(state, "channel.json"))
        assert stops() is None, "no relay state (집맥): nothing"

    def run(stdin, env=cli, args=()):
        # A job session's own flags would switch the long-reply check off in the child (seen from an auto tab, 11/04).
        base = {k: v for k, v in os.environ.items() if k not in ("MUNICE_AUTO_DISPATCH", "MUNICE_AMBIENT")}
        r = subprocess.run([sys.executable, "-I", os.path.realpath(__file__), *args], input=stdin,
                           capture_output=True, text=True, env={**base, **env}, timeout=10)
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
    closing = "\n".join(f"{label}: {under}" for label in ("무엇을", "왜", "확인", "원인", "조치", "재발 방지", "남은 것"))
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
