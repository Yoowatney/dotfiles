# Claude Code/Codex Stop 훅 기반 LLM Wiki 인제스트 설계

## 목적

launchd 없이 Claude Code와 Codex의 내부 훅만으로 LLM Wiki 인제스트를 실행한다. Codex는 `AGENTS.md` 지시에 따라 직접 작성한 일일 work-log를 기본 입력으로 사용하고, 기록이 누락된 턴에 한해서만 Codex transcript를 복구 입력으로 사용한다.

## 현재 상태

- Claude Code와 Codex 모두 `Stop` 훅에서 별도의 `stop-ingest.sh` 복사본을 호출한다.
- 두 복사본은 같은 쿨다운 파일과 같은 `llm-wiki-ingest.sh`를 사용하지만, 구현이 중복되어 변경 시 서로 달라질 수 있다.
- 인제스터의 Phase 1은 Claude Code transcript만 읽는다.
- Codex 작업은 에이전트가 직접 작성한 `work-logs/YYYY-MM-DD.md`를 Phase 1.5가 읽는 방식이다.
- launchd 서비스 `com.yoyoo.wiki-ingest`는 등록되어 있지 않다. 저장소의 plist는 과거 이력일 뿐 실행 경로가 아니다.

## 선택한 구조

### 1. 공유 인제스트 트리거

Claude Code와 Codex의 `Stop` 훅은 하나의 추적 가능한 공유 스크립트를 호출한다. 공유 스크립트는 다음을 책임진다.

- 1시간 쿨다운을 두 에이전트가 공유한다.
- 원자적 디렉터리 잠금으로 동시 실행을 막는다.
- 실제 인제스터 출력은 기존 로그 파일로 보낸다.
- 훅 호출 자체는 인제스트 실패 때문에 사용자 턴을 실패시키지 않는다.

### 2. Codex work-log 기준점 기록

Codex `UserPromptSubmit` 훅은 다음 정보를 세션별 상태 파일에 기록한다.

- 세션 ID와 가능하면 턴 ID
- transcript 경로와 현재 바이트 위치
- 오늘 work-log 파일의 현재 크기
- 작업 디렉터리

상태 파일은 `~/.local/share/llm-wiki/codex-turns/` 아래에 두며, 훅 입력 JSON 외의 Codex 내부 저장 형식에 의존하지 않는다.

### 3. Codex 누락 검사와 복구

Codex `Stop` 훅은 공유 인제스트를 호출하기 전에 해당 턴의 기준점과 현재 work-log 크기를 비교한다.

- work-log가 증가했으면 Codex가 직접 기록한 것으로 보고 복구를 생략한다.
- 증가하지 않았으면 transcript의 기준 바이트 이후 구간과 `last_assistant_message`를 복구 모델에 전달한다.
- 복구 모델은 기존 work-log 기록 규칙과 같은 기준으로 trivial 작업을 `NO_WORK` 처리한다.
- 기록할 가치가 있으면 기존 일일 work-log 형식으로 오늘 파일에 한 항목을 추가한다.
- transcript를 읽을 수 없으면 최종 응답만으로 판단하며, 입력이 부족하면 기록하지 않고 진단 로그를 남긴다.

이 방식은 transcript 전체 구조를 파싱하지 않는다. 공식 훅이 제공하는 `transcript_path`를 단순한 턴 원문 구간으로만 사용하므로 포맷 변경에 대한 결합을 줄인다.

## 설치와 설정

재실행 가능한 설치 스크립트가 다음 상태를 보장한다.

- Claude Code `Stop` 훅이 공유 트리거를 호출한다.
- Codex `UserPromptSubmit` 훅이 기준점 기록기를 호출한다.
- Codex `Stop` 훅이 누락 검사 후 공유 트리거를 호출한다.
- 기존 알림·Superset·프로젝트별 훅은 보존한다.
- launchd plist를 설치하거나 로드하지 않는다.

라이브 설정은 개인 정보가 포함될 수 있으므로 공개 dotfiles 저장소에 복사하지 않고, 설치 스크립트가 기존 JSON을 구조적으로 수정한다.

## 오류 처리

- 기준점 상태가 없으면 복구를 시도하지 않고 공유 인제스트는 계속 실행한다.
- 잘못된 훅 JSON은 진단 로그만 남기고 성공 종료한다.
- 복구 모델 실패나 시간 초과는 기존 work-log를 변경하지 않는다.
- 잠금이 이미 잡혀 있거나 쿨다운 중이면 인제스트를 중복 실행하지 않는다.
- Codex `Stop` 훅 stdout은 비워 유효하지 않은 JSON 출력 오류를 방지한다.

## 테스트

- 공유 트리거: 첫 실행, 쿨다운, 동시 잠금, 인제스터 실패 격리를 셸 테스트로 검증한다.
- 기준점 기록기: 신규/기존 work-log의 바이트 크기와 transcript offset 저장을 fixture로 검증한다.
- 누락 검사: work-log 증가 시 복구 생략, 미증가 시 복구 호출, trivial 결과, transcript 부재를 검증한다.
- 설치 스크립트: 임시 HOME의 Claude/Codex JSON에서 기존 훅 보존과 멱등성을 검증한다.
- 라이브 검증: 실제 훅 명령을 fixture JSON으로 호출하여 종료 코드 0, 빈 stdout, launchd 미등록을 확인한다.

## 제외 범위

- launchd 재활성화 또는 plist 설치
- Codex transcript 전체를 정규 스키마로 파싱하는 기능
- 기존 Claude Phase 1 transcript 처리 방식 변경
- `wiki/log.md` 저장 정책과 과거 로그 분할
