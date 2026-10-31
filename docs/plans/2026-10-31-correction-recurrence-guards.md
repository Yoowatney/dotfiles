# 지적 재발 방지 장치 구현 계획

> 실행: 이 칸에서 직접(Native). 단계 체크는 `- [ ]`.

**목표:** 반복된 지적 가운데 기계로 잡을 수 있는 것을 메모가 아니라 훅으로 막는다. Claude와 Codex, 새 세션과 이미 열린 세션에서 모두 작동해야 한다.

**구조:** 새 훅을 등록하지 않고 양쪽 도구에 이미 등록된 스크립트 안에 넣는다. 등록을 늘리면 Codex는 요유의 신뢰 승인이 있어야 돌고, 열린 세션에는 닿지 않는다. 스크립트 내용만 바꾸면 다음 호출부터 모든 세션에 적용된다.

| 넣을 곳 (이미 등록됨) | 시점 | 넣는 것 |
|---|---|---|
| `tools/agent-hooks/yoyoo-share-guard.py` | Stop | 칸 ID, 맥 이름 검사, 요유 몫 재질문 문구 수정 |
| `tools/agent-hooks/pane-label-nudge.sh` | UserPromptSubmit | 칸 판단 장전, 지적 상기 |
| `tools/claude/hooks/gcp-account-guard.py` | PreToolUse | 칸 판단 호출 |

**스펙:** 요유 지시서(칸 `지적 재발 방지 전면 검토`), 측정값은 `~/explain/` 결과 페이지.

## 전체 제약

- `auto-tab-close.py`, `pane-label`, `pane-spawn`은 다른 칸 소유라 고치지 않는다.
- `auto-tab-close.py`가 `share.leaves_yoyoo_work`, `share.STRIP`, `share.final_text`를 가져다 쓴다. 이 셋의 동작을 바꾸지 않는다. 바꾸면 결정이 남은 칸이 닫힌다.
- 칸 안내 명령은 `pane-spawn "<작업 이름>" "<지시>"`이다.
- 작업 중 리드가 범위를 넓혔다. 직접 띄우기를 막는 훅은 `리드 패널 자동 닫기` 칸이 만든 `no-sed-inplace.py`를 이 칸이 이어받아 `herdr pane run`, `send-text` 경로까지 넓힌다. 시험 칸이 남는 문제는 작업 6으로 다룬다.
- dotfiles는 PUBLIC이다. 비밀, 고객 정보, 가족 이야기를 시험 자료에 넣지 않는다.
- 훅은 실패하면 통과시킨다. 세션을 멈추지 않는다.
- 커밋은 경로를 지정해서만 하고, 직전에 `git status`를 다시 본다.

## 검토에서 볼 것

1. 넘겨받은 칸(herdr 에이전트 이름 있음), 자동 탭, `claude -p`, 서브에이전트는 칸 판단이 걸리지 않아야 한다.
2. 요유의 결정이 남은 보고는 같은 글을 다시 내보내지 않아야 하고, 그 칸은 닫히지 않아야 한다.
3. 칸 ID가 인라인 코드 안에 있어도 잡아야 한다. 코드 블록(로그) 안은 잡지 않는다.
4. 맥 이름 검사가 `회사서버맥`, `서버 맥북`을 잘못 잡지 않아야 한다.
5. 질문 메시지에 지적 상기가 붙어도 답만 하는 규칙을 깨지 않아야 한다.

---

### 작업 1: Stop 검사

**파일:** `tools/agent-hooks/yoyoo-share-guard.py` (시험은 같은 파일의 `--selftest`)

- [ ] 실패하는 시험: `w5:pKR`이 평문과 인라인 코드에 있으면 되돌리고 사유에 칸 이름이 들어간다. 코드 블록 안은 통과한다. `상시 맥`, `always-on Mac`, 단독 `서버맥`은 되돌리고 `회사서버맥`, `서버 맥북`, `집맥`은 통과한다.
- [ ] 구현: `pane_ids(text) -> list[str]`(원문에서 코드 블록만 제거), `pane_names(ids, env) -> dict`(`herdr pane list`, 실패하면 빈 사전), `bad_mac_names(text) -> list[str]`(`prose()` 뒤).
- [ ] `REASON` 문구 변경: 할 일이 있으면 하고 바뀐 보고를 쓰며, 할 일이 없으면 글을 쓰지 않고 끝낸다. 재작성 지시를 없앤다.
- [ ] `leaves_yoyoo_work`는 그대로 두고 `--selftest`와 `auto-tab-close.py --selftest`를 통과시킨다.
- [ ] 커밋.

### 작업 2: 지적 상기

**파일:** 새 `tools/agent-hooks/correction-nudge.py`, 수정 `pane-label-nudge.sh`, `pane-label-nudge.test.sh`

- [ ] 실패하는 시험: `reminder(text) -> str | None`이 행동 지적 문구에는 상기 글을, 일반 요청과 `[pane-tell:`, `[handoff:`로 시작하는 글에는 None을 준다. 시험 문장은 회사 일과 무관한 합성 문장만 쓴다.
- [ ] 구현과 연결: herdr 밖에서도 돌아야 하므로 `pane-label-nudge.sh`의 herdr 조기 종료보다 앞에서 호출하고, 칸 이름 안내 뒤에 이어 붙인다.
- [ ] 커밋.

### 작업 3: 칸 판단

**파일:** 새 `tools/agent-hooks/pane-fit-guard.py`, 수정 `tools/claude/hooks/gcp-account-guard.py`, `pane-label-nudge.sh`, `~/.claude/settings.json`(매처에 `Agent|Task|Workflow` 추가)

- [ ] 실패하는 시험: `check(payload, env, now) -> str | None`. 요유 메시지가 온 뒤 첫 구현 도구(`Edit`, `Write`, `MultiEdit`, `NotebookEdit`, `apply_patch`, 탐색용이 아닌 `Agent`·`Task`, `Workflow`, Bash의 `git commit`·`git push`·`gh pr create`·`gh pr merge`)나 5분이 지난 뒤의 도구 호출에서 사유를 한 번 돌려주고, 다시 호출하면 None을 준다. 면제: `HERDR_PANE_ID` 없음, sdk 실행, `agent_id`가 있는 서브에이전트, `MUNICE_AUTO_DISPATCH`·`MUNICE_AMBIENT`, herdr 에이전트 이름 있음, scratchpad·memory·explain·work-log·tmp 경로만 쓰는 호출, `pane-spawn` 호출.
- [ ] 구현: 상태는 `~/.local/state/pane-fit/<session_id>` 파일 하나다. 요유 메시지가 오면 `pane-label-nudge.sh`가 만들고, 묻고 나면 지운다. 파일 시각이 곧 메시지 시각이다. 7일 지난 파일은 메시지 때 지운다.
- [ ] `gcp-account-guard.py` 끝에서 호출한다. 예외가 나도 GCP 검사 결과에 영향을 주지 않는다.
- [ ] 커밋.

### 작업 4: 규칙

**파일:** `~/.codex/AGENTS.md`(다른 칸도 고치는 중이라 직전에 다시 읽고 Edit로 한 절만), 메모리

- [ ] 지적을 받으면 같은 종류 찾기, 원인 찾기, 고치기, 실제 확인까지 하는 규칙을 짧게 넣는다. 파일은 32KiB 아래로 유지한다.
- [ ] 메모리의 칸 규칙과 지적 규칙에 어떤 훅이 맡았는지 적는다.

### 작업 5: 실제 확인

- [ ] 훅을 고치기 전에 띄운 임시 Claude·Codex 칸(이미 열린 세션)과, 고친 뒤 새로 띄운 칸(새 세션)에서 칸 ID 되돌림, 결정만 남은 보고, 칸 판단, 지적 상기를 각각 확인한다.
- [ ] 최근 14일 최종 답변 4,226개와 재질문 102건을 새 판으로 다시 돌려 잘못 막는 수를 센다.
- [ ] 끝나면 임시 칸을 지운다.

### 작업 6: 시험 칸이 남지 않게 하기 (리드 지시로 추가)

**파일:** `tools/claude/hooks/no-sed-inplace.py`, 새 `tools/agent-hooks/pane-temp`, `pane-temp.test.sh`, `yoyoo-share-guard.py`

- [ ] `no-sed-inplace.py --selftest`: `tab create`, `pane run claude`, `send-text "claude --flag"`, 별칭 `cl`·`cla`, `then herdr`, `"$HERDR_BIN_PATH"`는 거절하고, 다른 칸에 보내는 문장(`claude 쪽 결과`, `codex-review 돌려 주세요`)과 일회성 명령은 통과한다.
- [ ] `pane-temp`: 이름 없는 칸을 칸의 셸로 띄워 별칭과 함수가 적용되게 한다. 에이전트가 끝나거나 `PANE_TEMP_TTL`이 지나면 칸 자신이 닫는다. 띄운 칸의 terminal을 `~/.local/state/pane-temp/<새 칸 terminal_id>`에 적는다. 이름 뒤 옵션은 사용법 오류다.
- [ ] `temp_reason`: 이 세션이 띄운 `임시 ` 칸이 열려 있으면 마지막 보고를 되돌린다. 띄운 칸은 `spawn-` 이름 또는 위 기록으로 판단하고, 기록이 없거나 다른 세션 칸이면 묻지 않는다.
- [ ] 실제 칸 확인: Claude와 Codex 각각 이름 없음, 별칭·`--no-daemon` 적용, TTL 닫힘, 에이전트 종료 시 닫힘, 다른 세션의 보고는 되돌아오지 않음.
