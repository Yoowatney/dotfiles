# 부하 세션 사용량별 실행기 선택 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 새 일반 부하 세션의 자동 실행기를 구독 한도 여유에 따라 고르고, 정책 하나로 기존 Codex 고정 선택으로 되돌린다.

**Architecture:** `agent-quota`가 로컬 사용량을 정규화하고 선택 근거를 JSON으로 반환한다. `pane-spawn`은 명시적 선택을 보존한 뒤 자동 경로만 이 결과를 적용한다. 상태줄은 Claude 공식 입력에서 한도만 저장하고, 인증정보나 회사별 운영 규칙은 저장소에 추가하지 않는다.

**Tech Stack:** Bash, Python 3 표준 라이브러리, unittest, 기존 herdr 가짜 실행기 테스트.

**Spec:** 요청에 지정된 2026-11-09 18:41:04 설계와 18:47:53 확인 보고. 회사별 원문과 계정 정보는 이 공개 저장소에 복사하지 않는다.

## Global Constraints

- 일반 `--kind auto`에만 적용한다. `--check`, 명시적 실행기, `--why`, 직접 지정한 모델·강도는 기존 동작을 유지한다.
- 주간 사용률에서 주간 경과율을 뺀 차이를 비교한다. 5%p 이상 앞서면 전환을 검토하고, 두 차이가 5%p 이내면 Codex를 유지한다.
- Claude 5시간 사용률은 80% 미만, 주간 사용률은 80% 이하를 허용한다. 10분 넘은 관측·다른 계정·지난 리셋은 unknown이다.
- 정책 기본값은 `quota-aware`. 로컬 정책 파일 값을 `codex-only`로 바꾸면 기존 선택으로 복구한다.
- 추가 결제 활성화·구입·계정 전환을 하지 않는다. 자동 선택에서 추가 결제 경로가 없다는 근거가 없으면 시작을 거부한다. 사용량을 못 읽었을 때도 안전한 Codex라는 근거가 있는 경우만 기본 선택한다.
- 사용량의 여유만으로 실행 중 과금을 차단할 수는 없다. 결제 경로 확인의 신뢰도와 실행 중 계정 설정 변경 위험은 최종 보고에서 명시한다.
- 시작·준비·지시 전달 실패는 다른 실행기로 재시도하지 않는다. 샌드박스·권한 규칙을 바꾸지 않는다.
- 기능 브랜치에만 커밋한다. 주 작업 폴더 설치, push, 집맥 동기화, 비공개 전역 규칙 수정, 보드·Slack 쓰기는 하지 않는다. 독립 리뷰는 메인이 붙인다.

## Review Focus

- 새로 수정된 오래된 기록이 최신 관측을 덮어쓰지 않아야 한다. 이벤트 시각으로 비교한다.
- 누락·손상·NaN·미래 관측·계정 교체·한도 만료를 0%로 간주하지 않아야 한다.
- 상태줄 반복 출력이 오래된 관측을 새 관측처럼 갱신하지 않아야 한다. API 응답 횟수 변화가 관측 갱신 기준이다.
- 직접 지정한 Codex 모델 인자가 Claude에 전달되거나 그 반대가 되어서는 안 된다.
- 사용량 수집·근거 로그 저장 실패가 예외로 자동 유료 실행을 허용해서는 안 된다.

### Task 1: 정규화·선택·안전 검사

**Files:** Create `tools/agent-hooks/agent-quota`, `tools/agent-hooks/agent-quota.test.sh`, `tools/agent-hooks/test_agent_quota.py`, `tools/agent-hooks/agent-quota-policy`.

**Interfaces:** `agent-quota select` → JSON `{kind, reason, policy, codex, claude}`와 종료 코드 0(선택)/1(거부). `agent-quota capture-claude`는 상태줄 JSON을 stdin으로 받는다. `agent-quota codex-week`는 기존 상태줄용 TSV를 반환한다. 비공개 관측과 결정 로그는 XDG state 아래 `agent-quota/`에 둔다.

- [x] RED: `test_select_claude_when_codex_ahead`, `test_both_near_limit_block`, `test_unknown_usage`, `test_codex_only`, `test_latest_event_not_mtime`, `test_billing_unknown_blocks`, `test_identity_reset_and_nonfinite`, `test_statusline_repaint_does_not_refresh`를 작성한다.
- [x] `bash tools/agent-hooks/agent-quota.test.sh` 실패를 확인한다.
- [x] Python 표준 라이브러리로 로컬 관측 파싱·원자적 비공개 저장·정책·선택을 구현한다. 인증 토큰은 저장하거나 출력하지 않는다.
- [x] 같은 테스트가 통과하는지 확인하고 커밋한다.

### Task 2: 실행기와 상태줄 연결

**Files:** Modify `tools/agent-hooks/pane-spawn`, `tools/agent-hooks/pane-spawn.test.sh`, `tools/claude/statusline-command.sh`, `tools/claude/statusline-command.test.sh`, `tools/agent-hooks/codex-week`.

**Interfaces:** Task 1의 JSON 중 kind만 실행에 쓰고 근거는 stderr와 비공개 JSONL 로그에 남긴다. 자동 Claude에는 모든 리드에서 Sonnet 5.5/high를 기본으로 준다.

- [x] RED: 안전한 Claude 전환·자동 차단 시 herdr 미호출·확인/명시적 모델 우선·codex-only·시작 실패 시 종류 유지 테스트를 추가한다.
- [x] 두 shell 테스트의 예상 실패를 확인한다.
- [x] 자동 실행기를 인자 구성 전에 고르고, 직접 모델/강도가 있으면 기존 Codex 선택을 유지한다. 상태줄 수집 실패는 표시를 깨지 않는다.
- [x] 두 테스트와 Task 1 테스트를 모두 실행하고 커밋한다.

### Task 3: 전체 검증·인계

**Files:** Update this plan; add `docs/worker-quota-routing.md` (운영·복구·안전 한계만 기록).

- [x] 문법 검사, 직접 영향받는 테스트, 저장소 테스트 전체를 실행하고 PASS/FAIL을 기록한다.
- [x] 전역 규칙과 주 작업 폴더를 바꾸지 않았는지 git diff/status로 확인한다.
- [x] 문서·계획을 커밋하고 커밋 SHA·테스트 결과·복구 명령·미해결 안전 조건을 보고한다.

### 검증 기록

- `bash tools/agent-hooks/agent-quota.test.sh`: 16개 PASS.
- `bash tools/agent-hooks/pane-spawn.test.sh`: PASS, `ok: pane-spawn`.
- `bash tools/claude/statusline-command.test.sh`: PASS, `all passed`.
- 변경한 Bash 파일의 `bash -n`, `git diff --check`: PASS.
- `954ada3`의 커밋 사본을 `tools/git-hooks/pre-push`에 입력: 테스트 스크립트 45개 PASS, 종료 코드 0. 실제 push는 하지 않았다.
- 두 문서의 `writing-plain-korean/check.py`: 직역과 20어절 초과 문장 없음.
- 프로젝트에 등록된 `verify-*` 스킬은 없었다. 별도 스킬 검증은 실행하지 않았다.
- 실제 설치된 `pane-spawn`은 원래 작업 폴더를 가리킨다. 기능 브랜치와 SHA-256이 달라 아직 설치되지 않았음을 확인했다.
- 회사맥의 설치·비공개 전역 규칙·집맥은 수정하지 않았다. 보드·Slack에 쓰지 않았다.

전체 테스트 재현 명령(커밋 사본 검사만 수행):

```bash
quota_sha=$(git rev-parse 954ada3)
printf 'refs/heads/feat/api-1096-quota-routing %s refs/heads/feat/api-1096-quota-routing 0000000000000000000000000000000000000000\n' "$quota_sha" | bash tools/git-hooks/pre-push
```

로그는 `/tmp/api-1096-full.log`에 남겼다. 이후 커밋은 운영 문서와 이 기록만 바꾼다.
독립 Claude 리뷰와 실제 계정에서의 검증은 메인이 진행한다.
실행 중 추가 결제까지 막는다는 보장은 이 테스트의 검증 범위에 포함되지 않는다.

## Self-review

명시적 선택·확인 세션·안전한 실패·근거 로그·복구를 각 작업에 배정했다.
실행 중 추가 결제를 절대로 막는 CLI 옵션은 확인하지 못했다.
시작 조건을 확인할 수 없으면 거부하고, 실행 중 결제 설정 변경의 한계는 리뷰에 올린다.
