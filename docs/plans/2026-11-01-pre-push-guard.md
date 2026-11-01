# dotfiles pre-push와 재귀 삭제 거부 구현 계획

**목표:** 시험을 통과하지 못한 커밋이 dotfiles 원격에 올라가지 못하게 하고, 거부된 명령을 형태만 바꿔 다시 실행하는 일을 Claude와 Codex 양쪽에서 막는다.

**구조:** pre-push는 push되는 커밋을 임시 폴더에 풀어 그 안의 시험만 돌린다. 재귀 삭제와 `git push --no-verify` 거부는 양쪽이 이미 신뢰한 공용 훅에 넣고, Claude 설정의 거부 목록에도 같은 규칙을 넣는다.

**기술:** bash, git hooks, python3 표준 라이브러리, Claude Code 권한 규칙.

## 전체 제약

- 미커밋 변경은 건드리지 않고 커밋하지 않는다. 커밋은 파일을 지정해서 올린다.
- 저장소가 공개이므로 비밀값, 회사 경로를 넣지 않는다. Claude 전역 설정은 이 저장소로 복사하지 않는다.
- macOS 기본 도구만 쓴다. `timeout`, `wait -n`처럼 맥마다 다른 것에 기대지 않는다.

## 검토 초점

- 삭제 push(sha가 전부 0)에는 시험을 돌리지 않는다.
- 시험이 stdin을 읽어도 push할 ref 목록을 빼앗기지 않는다.
- 시험이 멈추면 push도 영영 멈추지 않고 실패로 끝난다.
- Git이 넘긴 `GIT_DIR`이 시험 안의 `git`을 진짜 저장소로 데려가지 않는다.
- 시험을 하나도 찾지 못하면 통과가 아니라 실패다.

---

### 작업 1: pane-tell 시험의 경합 제거

**파일:** `tools/agent-hooks/pane-tell.test.sh`

- [ ] 원인 재현: `settled`가 끝난 뒤에도 분리된 전달 프로세스가 살아 읽기 횟수를 올리는지 확인한다.
- [ ] `settled`가 대기 파일뿐 아니라 그 프로세스의 종료까지 기다리게 고친다. 재시도는 넣지 않는다.
- [ ] 동시 6회 실행을 3번 반복해 전부 통과하는지 확인한다.
- [ ] 커밋한다.

### 작업 2: pre-push

**파일:** 새 파일 `tools/git-hooks/pre-push`, `tools/git-hooks/pre-push.test.sh`, 수정 `init.sh`, `verify.sh`

- [ ] 실패하는 시험을 먼저 쓴다. 임시 저장소와 맨 저장소로 실제 push해서 다음을 확인한다.
  - 시험이 통과하면 올라간다.
  - 커밋된 `*.test.sh`나 `--selftest`가 실패하면 막히고, 원격은 그대로이고, 실패한 시험 이름이 나온다.
  - 작업 폴더에서만 고친 시험과 추적되지 않은 시험은 결과에 영향을 주지 않는다.
  - 삭제 push는 시험 없이 통과한다.
  - 멈춘 시험은 제한 시간에 실패로 끝난다.
  - 시험을 찾지 못하면 막힌다.
  - 시험 안의 `git config`가 진짜 저장소를 바꾸지 않는다.
- [ ] 시험이 실패하는 것을 확인한다.
- [ ] `tools/git-hooks/pre-push`를 구현한다. 동시 실행 6개, 시험 하나당 제한 300초, 세션 환경변수(`HERDR_*`, `CLAUDE*`, `CODEX_*`, `MUNICE_*`) 제거, `GIT_*` 로컬 변수 제거.
- [ ] `init.sh`에 `core.hooksPath` 설정을 넣고 `verify.sh`에 확인을 넣는다.
- [ ] 새 클론에서 켜지고 막히는지 확인한다.
- [ ] 커밋한다.

### 작업 3: 공용 훅의 거부 확대

**파일:** `tools/claude/hooks/no-sed-inplace.py`

- [ ] selftest에 막아야 할 형태를 먼저 넣는다: `rm -r`, `-R`, `-Rf`, `-r -f`, `-vr`, `--recursive`, `/bin/rm -r`, `command rm -r`, `sudo rm -r`, `cd x && rm -r y`, `xargs rm -r`, `find -exec rm -r`, `bash -c 'rm -r x'`, `git push --no-verify`, `git -C x push --no-verify`.
- [ ] 허용할 형태도 넣는다: `rm -f a b`, `rm report.txt`, `trash x`, `git commit -m 'rm -r'`, `echo 'rm -rf'`, 히어독 본문, `trap 'rm -rf "$T"' EXIT`, `git rm -r x`, `git push`.
- [ ] 실패를 확인하고 구현한다. 거부 문구에 되돌릴 수 있는 `trash <경로>`를 적는다.
- [ ] 커밋한다.

### 작업 4: 자동 안내 문구

**파일:** `tools/agent-hooks/yoyoo-share-guard.py`

- [ ] selftest에 문구 확인을 먼저 넣는다.
- [ ] `DO_REASON`에 "권한 규칙이 거부한 명령은 형태를 바꿔 다시 실행하지 말고 그대로 보고한다"를 넣고, "다른 경로를 찾으세요"가 거부된 명령을 뜻하지 않게 좁힌다.
- [ ] 커밋한다.

### 작업 5: 설정과 켜기

- [ ] Claude 전역 설정의 거부 목록에 재귀 삭제 형태와 `git push --no-verify`를 넣는다.
- [ ] 집맥에 전역 `core.hooksPath`가 없는지 읽기로 확인하고, dotfiles 클론에 설정한다.
- [ ] 전역 설정이 집맥에 도착했는지 읽기로 확인한다.

### 작업 6: 실측

- [ ] Claude 새 세션과 Codex 새 세션에서 임시 폴더로 `rm -r`, 실패 시험 push, `--no-verify` push를 시도해 막히는지 확인한다.
- [ ] 멈춤 훅 문구를 두 모드로 확인한다.
- [ ] 코드 리뷰(`requesting-code-review`, `codex-review`)를 받고 반영한다.
