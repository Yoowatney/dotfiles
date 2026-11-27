# 메인 칸 전달 보류가 풀리지 않는 문제 고치기 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** main-refresh 가 메인 칸 앞 전달을 몇 시간씩 막지 않게 합니다. 늦었다는 알림도 같은 일로 메인 칸을 거듭 깨우지 않게 합니다.

**Architecture:** main-refresh 는 답이 올 수 없는 인계 요청을 버리고 다시 묻습니다. 전달 보류는 일하는 메인 칸을 10분까지만 막습니다. pane-relay-watch 는 한 칸 앞에 오래 기다리는 글을 한 건의 알림으로 묶습니다.

**Tech Stack:** bash 3.2(macOS), Python 3 표준 라이브러리, 기존 셸·unittest 시험.

**Spec:** 이 문서의 「설계」 절입니다.

## 설계

### 원인 (2026-11-27 기록)

1. 03:23에 `main-refresh --now`가 인계를 요청했습니다. 메인 칸은 인계를 저장한 뒤 확인하려고 `handoff load`를 직접 돌렸고, 그 명령이 인계 파일을 소비했습니다(`<pane>.md` → `<pane>.md.loaded`).
2. main-refresh 는 같은 요청 번호가 적힌 인계 파일을 30분 기다리다가 「인계가 저장되지 않아 건너뜀」으로 끝났습니다. 요청 파일(`main-refresh.refresh-request`)은 남았습니다.
3. workers-watch 는 몇 초 뒤 `main-refresh --now`를 다시 불렀습니다. 새 실행은 남은 요청을 「앞 실행의 인계 요청 응답을 계속 기다림」으로 이어받고 다시 묻지 않았습니다. 이 일이 11:36까지 16번 되풀이됐습니다.
4. 실행마다 `refresh-delivery.json`(전달 보류)을 처음부터 끝까지 유지했습니다. pane-tell 은 보류가 있는 동안 메인 칸 앞 글을 보내지 않았습니다. 실행 사이의 몇십 초 동안만 글이 5초에 한 건씩 나갔습니다.
5. pane-relay-watch 는 30분을 넘긴 글마다 알림을 따로 만들었습니다. 그래서 같은 보류 때문에 알림이 한 시간에 52건까지 생겼고, main-alert-wake 가 그때마다 메인 칸을 깨웠습니다.

11/25 20:12~21:57에도 같은 보류가 이어졌습니다. 그때는 메인 칸이 실행마다 30분 동안 쉬지 않아서 실행 세 번이 연달아 건너뛰었습니다.

### 접근

- **A. 묵은 요청을 다시 묻기 (고름).** 요청 파일이 30분 넘게 묵었고, 그 요청의 답이 없고, pane-tell 대기열에도 없으면 버리고 새 번호로 다시 묻습니다. 기준을 `MAIN_REFRESH_HANDOFF_WAIT`와 묶지 않는 이유가 있습니다. 시험은 이 값을 0.3초로 두고 「시간이 지난 감시의 다음 실행은 다시 보내지 않는다」를 확인합니다.
- **B. 보류가 일하는 메인 칸을 10분까지만 막기 (고름).** `blocked()`는 /clear 준비 전 보류(`clearing` 없음)의 파일이 10분 넘게 묵었고 메인 칸이 `working`이면 글을 보냅니다. main-refresh 는 메인 칸이 쉴 때마다 보류를 다시 써서, 나가는 인계 요청이 자기 10분을 얻게 합니다. 처음에는 요청 전 보류를 쉬는 칸에만 적용하는 방법(`idle_only`)을 만들었습니다. 그러나 이 방법은 갱신 중에 온 글이 새 세션에 닿는다는 보장(`main-refresh-race.test.py` 6건)을 깨서 접었습니다. 그 시험들은 몇 초 안에 끝나므로 10분 기준과 부딪히지 않습니다.
- **C. 알림을 받는 칸마다 한 건으로 묶기 (고름).** `waiting/`의 글은 받는 칸 순서대로 나가므로, 앞 글이 막히면 뒤 글도 같은 까닭으로 막힙니다. 그래서 한 칸 앞에 30분을 넘긴 글이 이어지는 동안에는 가장 오래된 글 하나만 알립니다. 그 칸 앞에 30분을 넘긴 글이 모두 나가면 다음 지연은 새로 알립니다.
- 뺀 방법: 보류를 아예 없애는 방법은 인계 요청이 마지막 글이어야 한다는 main-refresh 의 확인(`request_current`)을 깹니다. 늦은 글을 버리는 방법은 요청에서 금지했습니다. main-wait-guard 는 안전장치이므로 바꾸지 않습니다.

### 지키는 것

- 대기열 파일을 지우거나 옮기지 않습니다. 알림을 묶어도 글은 그대로 전달을 기다립니다.
- 요유가 친 글(`PANE_TELL_YOYOO_WORDS=1`)의 우선 전달과 /clear 직전 보류는 바꾸지 않습니다.
- 새벽 정기 실행(`--now` 없음)의 보류 방식은 바꾸지 않습니다.

## Review Focus

1. 요청 파일은 오래됐지만 인계가 이미 저장된 경우: 다시 묻지 않고 저장된 인계로 이어 가야 합니다(Task 1 시험).
2. 오래된 요청이 아직 pane-tell 대기열에 있는 경우: 다시 묻지 않아야 합니다(Task 1 시험).
3. 메인 칸이 `blocked`(권한 창)나 `unknown`일 때와 /clear 준비 중일 때: 10분이 지나도 글을 막아야 합니다(Task 2 시험).
4. 받는 칸 정보(`receivers/<name>`)를 읽지 못하는 글: 지금처럼 글마다 알려야 합니다(Task 3 시험).
5. 서로 다른 두 칸의 지연: 칸마다 한 건씩 알려야 합니다(Task 3 시험).

---

### Task 1: 묵은 인계 요청을 다시 묻기

**Files:** Modify `tools/agent-hooks/main-refresh`, Test `tools/agent-hooks/main-refresh.test.sh`

- [ ] Step 1: 시험을 씁니다. 요청을 한 번 보낸 뒤 요청 파일의 수정 시각을 31분 전으로 돌리고 다시 실행합니다. 기대값: `handoff save` 요청이 한 번 더 가고(`1`), 로그에 「다시 요청함」이 남고, 인계가 오면 /clear 합니다. 같은 조건에서 인계가 이미 저장돼 있으면 요청을 다시 보내지 않습니다. 같은 요청 번호가 `waiting/`에 있으면 다시 보내지 않습니다.
- [ ] Step 2: `bash tools/agent-hooks/main-refresh.test.sh`로 새 시험이 실패하는지 봅니다.
- [ ] Step 3: `request_may_be_queued` 정의 뒤에서, 이어받은 요청(`req=1`, `acknowledged` 없음)의 파일이 1800초 이상 묵었고 `memo_ready`가 아니고 `request_may_be_queued`가 아니면 요청 파일과 ack 파일을 지우고 `req=`로 둡니다. 로그: `앞 인계 요청이 30분 넘게 답이 없어 인계를 다시 요청함`.
- [ ] Step 4: 시험 전체가 통과하는지 봅니다.

### Task 2: 보류는 일하는 메인 칸을 10분까지만

**Files:** Modify `tools/agent-hooks/main-refresh`, `tools/agent-hooks/main-refresh-recovery.py`, Test `tools/agent-hooks/main-refresh-recovery.test.py`, `tools/agent-hooks/main-refresh.test.sh`

- [ ] Step 1: `main-refresh-recovery.test.py`에 `test_hold_keeps_a_working_pane_waiting_at_most_ten_minutes`를 씁니다. 보류 파일이 새것이면 `working`도 막고, 601초 묵으면 `working`은 보내고 `idle`·`done`·`blocked`·`unknown`은 막습니다. `prepare` 뒤(`clearing`)에는 묵어도 막습니다.
- [ ] Step 2: `main-refresh.test.sh`의 가짜 herdr 가 /clear 준비 전 보류 파일의 수정 시각을 기록합니다. 메인 칸이 1.2초 일하다 쉬면 수정 시각이 두 가지여야 합니다.
- [ ] Step 3: 두 시험이 실패하는지 봅니다.
- [ ] Step 4: `blocked()`는 `clearing`이 없고 `agent_status == "working"`이고 보류 파일이 600초 이상 묵었으면 False를 돌려줍니다. main-refresh 는 쉬기를 기다린 뒤 `holding=; hold_delivery`로 보류를 다시 씁니다.
- [ ] Step 5: `main-refresh.test.sh`, `main-refresh-recovery.test.py`, `main-refresh-race.test.py`, `pane-tell-order.test.py`가 모두 통과하는지 봅니다.

### Task 3: 한 칸 앞의 지연은 알림 한 건

**Files:** Modify `tools/agent-hooks/pane-relay-watch.py`, `tools/agent-hooks/pane-tell`(머리 설명 한 줄), Test `tools/agent-hooks/pane-relay-watch.test.py`

- [ ] Step 1: `test_one_receiver_backlog_alerts_once_until_it_drains`를 씁니다. 같은 칸 앞 `waiting/` 글 셋이 30분을 넘기면 알림은 가장 오래된 글 하나입니다. 그 글이 나가도 남은 글로는 새 알림이 없습니다. 모두 나간 뒤 새로 30분을 넘긴 글은 알립니다. `test_two_receivers_each_alert_once`, `test_unreadable_receiver_alerts_each_payload`도 씁니다.
- [ ] Step 2: 새 시험이 실패하는지 봅니다.
- [ ] Step 3: `scan()`에서 `waiting/*` 후보의 받는 칸을 `receivers/<name>`의 두 줄(터미널, 소켓)로 읽습니다(`Path.read_text`가 아닌 `open`). 앞 검사에서 알렸거나 묶은 글이 남아 있는 칸, 또는 이번 검사에서 이미 알린 칸의 글은 이벤트를 만들지 않고 `accepted`에만 넣습니다. 후보는 이름순(도착 순)으로 봅니다.
- [ ] Step 4: `pane-relay-watch.test.py --selftest`와 `main-alert-hooks.test.py`가 통과하는지 봅니다.
