# 퇴근 때 밑 칸을 집맥에서 이어 가기 (api#1049 1단계) 구현 계획

> 실행: 이 계획을 쓴 칸이 직접 구현한다(executing-plans 방식). 단계는 `- [ ]`로 적는다.

**목표:** 요유가 "퇴근"이라고 하면 메인 칸은 회사맥에 그대로 두고, 메인 칸의 밑 칸만 WIP 커밋·push와 인계 메모를 남긴 뒤 집맥 workers 서버의 새 칸이 그 일을 이어 간다. 집맥 칸의 보고와 알림은 회사맥이 깨어 있을 때 메인 칸에 전해진다.

**구조:** 새 스크립트 `workers-move`(dotfiles, python)가 회사맥 쪽 전부를 한다: 밑 칸에 멈춤 요청, 메모 수집, git 확인과 push, ssh로 집맥에 폴더 준비와 메모 전송, 집맥에서 `pane-spawn --opener`로 새 칸 열기, 회사맥 칸 닫기, 그리고 보고 다리(`bridge`). 집맥에서 연 칸은 에이전트 이름에 `r`이 붙어(`spawn-<메인 칸 터미널>-r…`) auto-tab-close와 workers-watch가 "회사맥 메인 칸의 칸"으로 알아본다. 메인 칸이 맡던 Slack 스레드는 집맥의 `state.py release`로 ambient에 돌려준다.

**기술:** python3(macOS 3.9 호환), bash, herdr 0.9.0 CLI, ssh(`~/.config/herdr/remote-host`). 시험: 각 python 파일의 `--selftest`, `pane-spawn.test.sh`, munice-claude ambient의 `tests/test_state.py`, `tests/test_laptop_threads.py`.

**설계:** api#1049 본문과 설계 답(세션 6d120aae, 2026-11-06T09:43:50Z)의 (d). 요유가 11/06 20:36에 ✅.

## 지킬 것

- 메인 칸의 밑 칸(에이전트 이름 `spawn-<메인 칸 터미널 id>-`)만 옮긴다. 다른 리드의 칸, 확인 칸(`chk`), 요유가 연 칸은 건드리지 않는다.
- 전역 설정·훅·스킬은 회사맥에서만 고친다. 집맥은 sync-home 4단계(`git pull --ff-only`)로 dotfiles와 munice-claude를, 5단계로 `~/.claude/skills`를 받는다.
- 집맥에서만 할 수 있는 일(launchd 등록)은 되돌리는 명령과 함께 보고한다.
- Slack에는 아무것도 쓰지 않는다. `~/.dotfiles`는 공개 리포라 비밀이나 고객 정보를 넣지 않는다.
- 시험 칸은 `임시 ` 이름으로 열고 끝나면 닫는다. 실제 메인 칸과 다른 리드에 닿지 않게 `XDG_STATE_HOME`을 시험 폴더로 돌린다.
- `git commit`과 `git push`는 따로 실행한다.

## 정한 것 (설계안과 다른 점 포함)

| 경우 | 결과 |
|---|---|
| 옮기는 순서 | 집맥 칸을 먼저 열고, 열린 것을 확인한 뒤 회사맥 칸을 닫는다. 설계안은 닫기가 먼저였다. 바꾼 이유: 집맥 칸이 안 열리면 회사맥 칸을 그대로 두어 일을 잃지 않는다. 메모를 쓴 회사맥 칸은 입력을 기다리는 상태라 그사이 덮개를 닫아도 스스로 일하지 않는다(모니터·백그라운드 작업은 요청문에서 멈추게 한다) |
| 밑 칸이 할 일 | 하던 도구 호출이 끝나면 멈추고, 이 일의 브랜치에 WIP 커밋, 스레드 감시·Monitor·백그라운드 작업 정지, 인계 메모 답(첫 줄 `[인계 메모] 옮김` 또는 `[인계 메모] 옮기지 않음: <이유>`, `폴더: <절대경로>` 줄 포함) |
| 옮기지 않는 칸 | 칸이 스스로 정한 것(기다리기만 함, 브라우저 QA, 요유 계정 GCP 단계, 끝난 일), 15분 안에 메모가 없는 칸, 살아 있는 하위 칸이 있는 칸, 폴더에 커밋하지 않은 변경이 있는 칸, 기본 브랜치에 push 안 된 커밋이 있는 칸, 분리된 HEAD, 회사맥 원본 체크아웃에서 기능 브랜치 작업 중인 칸, 집맥에 같은 경로가 다른 브랜치로 있는 칸, push 실패. 이런 칸은 회사맥에 그대로 두고 요약에 이유를 쓴다 |
| push | 기능 브랜치가 원격보다 앞서 있거나 원격이 없으면 workers-move가 `git push -u origin HEAD`를 한다(Codex 칸은 네트워크가 막혀 있다). 커밋은 하지 않는다: 공유 체크아웃에서 남의 파일을 커밋할 수 있어서다 |
| 집맥 폴더 | 그 경로가 있으면 같은 브랜치인지 보고 `--ff-only`로 맞춘다. 없으면 집맥의 같은 경로 리포에서 `fetch` 뒤 `worktree add`. git 폴더가 아니면 그 경로가 집맥에 있어야 한다 |
| 회사맥 칸의 메모 답 | `{state}/orchestrator/moving/<터미널>` 표시가 있는 동안 auto-tab-close는 보고를 파일에만 남기고 아무에게도 보내지 않으며 칸을 닫지 않는다. 확인 칸이 열리지 않는다 |
| 회사맥 칸 닫기 | `reported/<터미널>`, `reported/<세션>`을 먼저 찍고 닫아서 workers-watch가 "보고 없이 사라졌습니다"를 보내지 않는다 |
| 집맥 칸 이름 | `pane-spawn --opener term_<메인 칸>`: 부르는 칸 없이 집맥 workers에 칸마다 새 탭, 에이전트 이름 `spawn-<메인 칸>-r<난수>`, 메인 칸용 보고 첫 줄 안내를 붙인다. workers가 없으면 실패한다 |
| Codex 칸 | 집맥에서는 언제나 Claude로 연다(리뷰 반영). Codex 칸은 네트워크가 막혀 push를 못 하고, 집맥의 새 워크트리에서는 신뢰 창이 지시문을 먹는다. pane-spawn이 제한 시간에 걸리면 칸이 열렸는지 모르므로 `unknown`으로 둔다 |
| 집맥 칸의 보고 | auto-tab-close: 받을 리드가 없으니 날짜 파일이 전달이다. `reported` 표시를 찍고, 보고대로 닫거나 둔다 |
| 집맥 칸의 알림 | workers-watch: `r` 칸은 리드가 닫힌 칸으로 보지 않는다(리드는 회사맥에 있다). 누구에게도 못 전한 알림은 herdr 알림 대신 날짜 파일에 `[workers-watch]` 항목으로 남긴다 |
| 보고 다리 | 회사맥 workers-watch가 1분마다 `workers-move bridge`를 부른다. 열린 이동이 없으면 바로 끝난다. ssh 한 번으로 집맥 workers 칸 목록과 날짜 파일의 새 부분을 받아, 옮긴 칸의 항목만 회사맥의 같은 날짜 파일 `~/.local/state/pane-reports/YYYY-MM-DD.md`에 주소 `집맥:workers/<칸>`으로 복사하고(구현 때 바꿈: `전체 보고: <파일> (<시각> 항목)`을 읽는 도구들이 그대로 찾는다) 메인 칸에 전한다. 끝난 보고는 auto-tab-close의 `route_main_report`로 보내 확인 칸 흐름을 그대로 탄다. 확인 칸을 열기 전에 회사맥 폴더를 `--ff-only`로 집맥 브랜치에 맞추고, 못 맞추면 확인 칸 없이 바로 전한다. 못 보낸 항목은 대기열에 남아 다음 번에 다시 간다. 집맥 칸이 사라지고 대기열이 비면 그 이동은 끝난다 |
| 집맥 칸에 답하기 | `workers-move tell <집맥 칸> "<글>"`(ssh 위의 pane-tell). 다리가 전하는 글마다 이 명령을 적는다 |
| 스레드 돌려주기 | 집맥 `state.py release`: `handoff`가 있는 스레드마다 `handoff`를 지우고 `seenTs`를 지금으로, `releasedAt`을 찍는다. 이미 메인 칸 쪽이 본 글을 집맥이 다시 처리하지 않게 하기 위해서다. 회사맥 relay가 다음 sync 때 메인 칸에 "집맥이 맡음"을 알릴 때, `releasedAt`이 있으면 "퇴근 때 집맥에 돌려준 스레드"라고 쓴다 |
| 구현·리뷰 때 더한 것 | 질문 창에서 멈춘 칸은 묻지 않는다(입력이 질문 창의 답이 된다). 집맥 칸을 여는 도중 ssh가 끊기거나 실행이 죽으면 `unknown`: 다시 묻지도, `resume`하지도 않는다. 기다리는 동안 회사맥이 잠들었다 깨면 아무 칸도 옮기지 않는다. `moving` 표시는 `[인계 메모]`가 든 답만 막는다. 다리는 집맥 칸을 여는 중에는 읽지 않고, 칸이 사라진 뒤 끝까지 읽은 다음에만 끝낸다. `release`는 메인 칸이 받지 못한 부름을 남기고, `state-remote.sh release`가 회사맥에 `off-duty` 표시를 남겨 그동안 메인 칸이 부름을 받지 않고 스레드도 잡지 않는다(요유가 30분 뒤 키보드를 쓰면 풀림). workers-watch는 다리를 분리해 띄운다(시간 제한에 잘려 두 번 보내지 않게) |
| 실행 방식 | `workers-move go`는 백그라운드 실행을 하나 띄우고 바로 끝난다. 끝나면 요약을 메인 칸에 pane-tell로 보내고 파일에도 남긴다. 다시 돌리면 남은 칸만 이어 한다(잠금으로 하나만 돈다) |

범위 밖(2단계나 따로 정할 일): 메인 칸째 옮기기, 집맥 fork-watch·main-refresh, 집맥에서 Slack으로 바로 알리기, 옮긴 칸을 아침에 회사맥으로 되가져오기.

## 리뷰 초점

1. 메모를 쓰는 도중 덮개가 닫혀 실행이 멈췄다 깬 경우: 같은 칸이 두 번 열리지 않아야 한다. 이동 기록(`step`)으로 고정하고 selftest로 확인한다.
2. 메인 칸 입력창이 차 있어 다리가 못 보낸 경우: 항목이 사라지거나 두 번 복사되지 않아야 한다. selftest.
3. 집맥 ssh가 끊긴 경우(go 도중, bridge 도중): go는 아무 칸도 닫지 않고 끝나고, bridge는 위치를 그대로 둔다. selftest.
4. 다른 리드의 칸과 확인 칸이 목록에 섞인 경우: 요청이 가지 않아야 한다. selftest.
5. 공유 체크아웃에 남의 변경이 있는 칸: 옮기지 않고 커밋도 하지 않는다. selftest(임시 git 리포).

---

### 작업 1: 집맥 칸 표시와 pane-spawn `--opener`

**파일:** `tools/agent-hooks/herdr_peers.py`, `tools/agent-hooks/pane-spawn`, `tools/agent-hooks/pane-spawn.test.sh`

**내놓는 것:** `herdr_peers.remote_pane(name) -> bool` (`^spawn-[0-9A-Za-z]+-r`), `pane-spawn --opener <terminal>`.

- [ ] selftest: `remote_pane("spawn-65f2-r123")` 참, `spawn-65f2-123`·`spawn-65f2-h1`·`spawn-65f2-chk1`·None 거짓, `opener("spawn-65f2-r1") == "term_65f2"`.
- [ ] pane-spawn.test.sh: `HERDR_PANE_ID` 없이 `--opener term_abc` → workers에 `tab create`, `agent start spawn-abc-r`, `pane get`·`pane-label --lead` 없음, 보고 첫 줄 안내가 지시에 붙음, 출력 `workers/<id>`. workers가 꺼져 있으면 exit 1이고 default 서버에 칸을 열지 않음. `--opener`와 `--check`를 같이 주면 exit 2.
- [ ] 실패 확인, 구현, 통과 확인, 커밋.

### 작업 2: auto-tab-close가 옮기는 칸과 집맥 칸을 안다

**파일:** `tools/agent-hooks/auto-tab-close.py`

- [ ] selftest: `moving/<터미널>` 표시가 있는 칸의 보고는 저장되고, 아무에게도 가지 않고(pane-tell 호출 없음), 닫히지 않는다. 집맥 칸(`spawn-M-r1`, 리드 없음)의 끝난 보고는 저장되고 `reported/<터미널>`이 찍히고 닫힌다. `요유 몫`이 남은 집맥 칸 보고도 `reported`가 찍히고 닫히지 않는다.
- [ ] 실패 확인, `finish()`에 두 갈래 추가, 통과 확인, 커밋.

### 작업 3: workers-watch가 집맥 칸 알림을 파일에 남기고 다리를 부른다

**파일:** `tools/agent-hooks/workers-watch.py`

- [ ] selftest: 집맥 칸(`spawn-M-r1`, 리드 없음)은 "리드가 닫혔습니다"를 받지 않고 1시간 뒤 닫히지도 않는다. 질문 창 60초·보고 없이 15분·에이전트 끝남·사라짐 알림은 herdr 알림 대신 날짜 파일에 `## HH:MM:SS workers/<칸> <이름>` + `[workers-watch] <글>` 항목으로 남는다. 보통 칸의 알림 경로는 그대로.
- [ ] `main()`이 tick 뒤 `WORKERS_WATCH_MOVE`(기본 `HERE/workers-move`) `bridge`를 90초 제한으로 부른다. 실패해도 조용히 넘어간다.
- [ ] 실패 확인, 구현, 통과 확인, 커밋.

### 작업 4: `workers-move`

**파일:** 새 `tools/agent-hooks/workers-move`(python, 실행 권한, `--selftest`)

**쓰는 것:** `herdr_peers`(main_terminal, all_panes, socket_of, herdr, opener, state_dir), auto-tab-close의 `last_reply`, `route_main_report`, `short_report`(importlib로 읽음), `pane-spawn --opener`(집맥).

**명령:** `go`(백그라운드 실행 시작), `list`(할 일만 출력), `bridge`, `tell <칸> <글>`, `--selftest`. 시험용 env: `WORKERS_MOVE_SSH`(ssh 대신 부를 명령), `WORKERS_MOVE_TELL`(pane-tell 대신), `WORKERS_MOVE_WAIT`(메모 기다리는 초, 기본 900), `WORKERS_MOVE_POLL`, `WORKERS_MOVE_REMOTE`(ssh 대상), `WORKERS_MOVE_FG=1`(go를 앞에서 실행).

**상태:** `{state_dir}/../workers-move/moves.json`(회사맥 터미널 → pane, agent, session, kind, label, cwd, folder, branch, step `asked|kept|opened|closed|done`, reason, remote, asked_at, opened_at), `bridge.json`(파일별 위치, 대기열), `run.lock`, `run.log`, `last-summary.txt`.

- [ ] selftest(가짜 herdr·ssh·pane-tell, 임시 git 리포와 bare origin):
  - 후보: 메인 칸의 칸만, 확인 칸·다른 리드 칸·이미 옮긴 칸 제외.
  - 메모 파싱: `[인계 메모] 옮김`, `옮기지 않음: 이유`, `폴더:` 줄, 마커 없음은 기다림.
  - git 판정: 깨끗한 기능 브랜치(push 함), 더러운 폴더(옮기지 않음, 커밋 없음), 기본 브랜치에 push 안 된 커밋(옮기지 않음), 분리된 HEAD, git 아닌 폴더(옮김).
  - 순서: 집맥 준비 → 메모 전송 → 집맥 칸 열기 → `reported` 표시 → 회사맥 칸 닫기. 집맥 열기 실패면 회사맥 칸이 남고 `kept`.
  - 다시 실행: `closed` 칸은 다시 열지 않음.
  - ssh 안 됨: 아무 칸에도 요청하지 않고 끝남.
  - 다리: 새 항목 복사·전달, 보내기 실패면 대기열에 남고 다음 번에 한 번만 복사, 다른 칸 항목은 무시, `[workers-watch]` 항목은 알림 글로, 집맥 칸이 사라지고 대기열이 비면 `done`.
- [ ] 실패 확인, 구현, 통과 확인, 커밋.

### 작업 5: 스레드 돌려주기 (munice-claude)

**파일:** `.agents/skills/agent-board/ambient/state.py`, `laptop_threads.py`, `tests/test_state.py`, `tests/test_laptop_threads.py`

- [ ] test_state: `release`가 handoff 있는 스레드만 바꾸고(handoff 삭제, seenTs = 지금, releasedAt, next), 키 목록을 JSON으로 출력, 없으면 `[]`.
- [ ] test_laptop_threads: `releasedAt`이 있는 행의 되돌림 글은 "퇴근 때 집맥에 돌려준 스레드"라고 쓴다.
- [ ] 실패 확인, 구현, `python3 -m pytest tests/test_state.py tests/test_laptop_threads.py`, 커밋.

### 작업 6: 집맥 launchd, 규칙, 실제 시험, 착지

**파일:** `~/.claude/skills/main-orchestrator/SKILL.md`(퇴근 절차 한 절), 집맥 `~/Library/LaunchAgents`(링크 둘).

- [ ] 리뷰(requesting-code-review + codex-review) 뒤 dotfiles와 munice-claude를 main에 반영하고 push. 집맥이 pull했는지 확인.
- [ ] 집맥: `com.yoyoo.herdr-workers`, `com.yoyoo.workers-watch` 링크와 bootstrap. 되돌리기: `launchctl bootout gui/$(id -u)/<label>`, 링크 삭제.
- [ ] 실제 시험(회사맥에서 `XDG_STATE_HOME`을 시험 폴더로, 그 안의 `orchestrator/main`은 `임시 ` 칸의 터미널): 시험용 밑 칸 하나(시험 브랜치 워크트리)를 열고 `workers-move go` → 메모, push, 집맥 칸 열림, 회사맥 칸 닫힘, 집맥 칸 보고, 다리로 시험 메인 칸에 도착. 실패 흐름: ssh 대상이 틀린 경우 아무 칸도 닫히지 않음. 끝나면 시험 칸, 집맥 칸, 시험 브랜치, 집맥 워크트리 정리.
- [ ] SKILL.md 퇴근 절 추가(파일을 다시 읽고 고친다: 메인 칸이 같이 고치는 파일이다).
