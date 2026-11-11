# 일반 세션 인계 구현 계획

> **For agentic workers:** Use superpowers:executing-plans inline. Check each step with TDD.

**목표:** 일반 인계에서 누락을 검사하고, 검증한 코드와 다음 세션의 코드를 구분한다.

**구조:** 기존 셸 진입점은 시작 메시지 전송을 맡는다. 표준 라이브러리 Python 보조 코드가 Markdown에 붙인 JSON을 검사하고 원자적으로 저장한다. Codex 시작 연결은 재실행 가능한 설치 스크립트로 추가한다.

**기술:** Bash, Python 3 표준 라이브러리, Git, herdr.

**명세:** 승인된 일반 인계 보완. 본문 유지, 필수 항목 검사, 저장소별 현재/검증 상태 구분, 변경 시 재검증 표시, 옛 메모와 발췌 호환.

## 제약

- 메인 자동 인계와 기기 이동 양식은 변경하지 않는다.
- 작업 번호 통합, 대기 조건 통합, 재시도 조회는 제외한다.
- 공개 저장소에는 실제 인계, 원문, 사용자 정보, 비밀값을 넣지 않는다.
- 전역 설정은 회사맥에서만 수정한다.

## 검토 초점

- 누락·손상된 JSON은 이전 메모를 보존해야 한다.
- 저장 도중 교체 실패는 기존 메모를 보존해야 한다.
- HEAD가 같아도 미커밋 변경 또는 검증 상태 미상은 이전 검증을 재사용하면 안 된다.
- 긴 메모에서도 검증 경고는 시작 문맥에 남아야 한다.
- Codex 재시작은 같은 인계를 두 번 주입하거나 다른 세션 원문을 읽으면 안 된다.

## 1. 저장과 읽기

대상: `tools/agent-hooks/handoff`, `handoff-state.py`, `handoff-state.test.py`, `handoff.test.sh`.

- [ ] 필수 항목, 여러 저장소, 코드 없음, 미검증, 저장 실패, 커밋/브랜치/작업폴더 변경, 옛 메모 시험을 먼저 작성한다.
- [ ] 새 시험이 미구현 때문에 실패하는지 실행해 기록한다.
- [ ] `handoff save`가 `handoff` JSON 펜스의 task/completed/remaining/verification/resume/code/repositories를 검사한다. 저장소 입력은 path/verified_commit/verified_dirty/evidence다. 현재 상태는 자동 수집한다.
- [ ] 같은 폴더의 임시 파일에 쓰고 fsync 후 replace한다. 읽기는 오래된 메모를 무시하고 성공한 출력만 소비한다.
- [ ] 메인 terminal 식별이 확인되면 자유문 저장을 유지한다. 확인 실패는 일반 검사로 처리한다.
- [ ] 기존 셸 시험과 새 Python 시험을 실행한다.

## 2. 시작 연결과 안내

대상: `tools/agent-hooks/install-handoff.py`, `install-handoff.test.py`, `pane-label-nudge.sh`, `pane-label-nudge.test.sh`, `init.sh`, 전역 `~/.codex/hooks.json`, `~/.codex/AGENTS.md`.

- [ ] 임시 설정에서 설치 재실행 시 다른 훅을 유지하고 하나만 등록하는 시험을 먼저 실행한다.
- [ ] Codex용 `handoff session codex`는 시작 때 명시 인계를 우선 읽는다. 저장하지 않은 같은 pane의 이전 세션 발췌는 Codex 원문 형식으로 읽는다.
- [ ] 세션 식별자는 transcript 경로가 없는 경우 Codex 원문 session_meta를 대조해 찾는다.
- [ ] 공통 안내와 오래된 Codex 제외 안내를 갱신한다.
- [ ] 설치·시작·회귀 시험을 실행한다.

## 3. 실제 검증과 반영

- [ ] Claude와 Codex의 이미 열린 시험 세션을 변경 전 만들고, 변경 후 저장·읽기를 확인한다.
- [ ] Claude와 Codex의 새 시험 세션에서 저장·재시작·읽기를 확인한다. 모두 pane-temp로 열고 끝나면 닫는다.
- [ ] 정상·실패 흐름과 메인 자동 인계 회귀 결과를 `/tmp/codex/2026-11-11-session-handoff/`에 저장한다.
- [ ] 독립 검토에 diff와 검증 자료를 넘기고 중요 지적을 수정한다.
- [ ] 관련 파일만 별도 커밋, 실제 사용 경로에 반영 후 push한다. 전역 변경은 복구용 사본과 diff를 비공개 근거 폴더에 둔다.
