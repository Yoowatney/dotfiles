# AGY Worker I/O Implementation Plan

> 2026-11-28 갱신: 아래 Claude 확인 세션 설명은 당시 계획이다. 현재 `--check`와 자동 확인은 Codex로 열고, `codex-reserve`와 `HERDR_CHECK_TARGET`을 쓴다. 일반 코드 리뷰 규칙은 유지한다.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** AGY 작업 칸에서 개발용 네트워크와 후속 지시를 받고, 지정한 모델 정책을 따른다.
**Architecture:** 세션맥의 새 AGY 부하 세션은 사용자 지시에 따라 terminal sandbox 없이 실행한다. 다른 호스트는 기존 sandbox를 유지한다. 입력 전달은 기존 잠금·큐를 유지하며 AGY의 실제 빈 입력창만 인식한다. 모델 선택은 pane-spawn 한곳에서 처리한다.
**Tech Stack:** Bash, Python, AGY 1.5.16/1.5.17.
**Spec:** 이 문서의 요구와 제약.

## Global Constraints
- 기본 normal은 gemini-4-hydrogen-xhigh, simple은 gemini-4-hydrogen-medium, complex는 gemini-4-neon-high이다.
- Carbon은 사용자가 시킨 작업의 명시적 --carbon에만 gemini-4-carbon-high를 쓴다. --check는 Claude를 유지한다.
- OAuth와 원본 자격 파일의 도구 경로, 셸의 리터럴 경로·글롭·알려진 재귀 검색을 훅으로 검사한다. 대소문자와 cd 뒤 상대 경로도 검사한다. Slack 게시 금지와 기존 보호 훅은 유지한다. 임의 코드·계산한 경로·모든 셸 문법을 해석하는 격리 경계가 아니며, sandbox 없이 모든 파일 접근을 차단한다고 주장하지 않는다.
- 기본 실행 정책은 claude-first이다. AGY는 명시적으로 고른 작업에서만 사용하며 --check는 Claude를 유지한다.
- 진행 중인 다른 칸의 입력은 건드리지 않는다. 알 수 없는 입력 상태는 전달을 보류한다.
- 기존 사용량 검사·보고·종료 흐름을 유지한다.

## Approaches
최초 도메인 허용 방안은 후속 사용자 지시로 교체됐다. 세션맥만 enableTerminalSandbox=false, accept-edits, always-proceed로 실행하고 --sandbox를 생략한다. 공유 훅과 deny 규칙을 그대로 실행하며 원본 인증 경로의 글롭과 검색을 보강한다. GitHub 인증 파일 자체를 새로 복사하거나 권한을 넓히지 않는다.

## Review Focus
- 빈 입력창과 여러 줄 초안, 선택 대화창, 기록 속 > 문자를 구별한다.
- 전송 실패와 스크롤된 화면은 기존 큐에 남는다.
- --carbon과 check/legacy 옵션의 모순은 조용히 무시하지 않는다.
- 네트워크 제한은 세션맥에서 제거되지만 Slack 게시 요청은 계속 거절된다.
- GitHub 인증값 직접 읽기와 GCP 계정 전환은 계속 거절된다.

### Task 1: Model selection
**Files:** tools/agent-hooks/pane-spawn, tools/agent-hooks/pane-spawn-agy.test.sh, tools/agent-hooks/pane-spawn-runner.test.sh.
**Interfaces:** pane-spawn --complexity and --carbon produce the selected native model argument.
- [x] Add tests for the three complexity levels, explicit Carbon, invalid combinations and unchanged Claude checks; run to observe failures.
- [x] Update option parsing, help and mapping; run all pane-spawn tests.
- [ ] Commit the tested change.

### Task 2: AGY input delivery
**Files:** tools/agent-hooks/pane-input-box.py, tools/agent-hooks/pane-tell, tools/agent-hooks/pane-tell-agy.test.sh, tools/agent-hooks/test_pane_input_box.py.
**Interfaces:** pane-input-box.py agy exits 0 only for a live empty AGY input; pane-tell accepts herdr agent kind agy.
- [x] Capture the native input frame, add parser and routing tests for empty/draft/dialog/scroll/queue cases; observe failures.
- [x] Add the bounded parser and permit AGY receivers without changing other runtimes; run pane-tell tests.
- [ ] Commit and test an actual AGY follow-up message after review.

### Task 3: Session Mac sandbox policy and authentication guards
**Files:** tools/agent-hooks/agy-hooks.py, tools/agent-hooks/test_agy_hooks.py, tools/agent-hooks/pane-spawn, tools/agent-hooks/pane-spawn-agy.test.sh.
**Interfaces:** native command hooks preserve deny behavior; session-only launch settings leave persistent and other-host settings intact.
- [ ] Reproduce GitHub and Slack 403 and config denial with no sensitive output; save only statuses.
- [ ] Disable the sandbox only for the session Mac, retain hooks and explicit credential denial. Test git fetch, gh read and Slack attachment read in a real worker.
- [ ] Verify auth-read, Slack publication, sandbox-bypass and GCP-switch denial; request independent Claude review, apply reviewed changes, run final worker probe.

### Task 4: Native file-edit tool compatibility
**Files:** tools/agent-hooks/agy-hooks.py, tools/agent-hooks/test_agy_hooks.py.
- [x] Capture current write_file(Path, Content) and edit_file(Path, Target, Replacement) schemas.
- [x] Reproduce unsupported-tool denial and translate both into the existing shared Write/Edit guards.
- [ ] Test an actual repository fixture edit and gh pr view in a fresh worker, with cleanup.
