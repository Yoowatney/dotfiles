# Codex 확인 세션의 읽기 전용 분석 연결

**목표:** `pane-spawn --check --analysis`에서 Honeycomb과 Stripe 조회를 제공한다. 확인 대상 폴더에 대한 프로세스 트리 전체의 쓰기 금지는 유지한다.

**설계:** Codex 실행 인자에 공식 HTTP MCP 주소와 읽기 도구의 명시적 허용 목록을 넣는다. 이 정책은 문맥 축소와 독립적이며 full 모드와 설정 오류에도 적용한다. 호출자가 넘긴 설정 뒤, `--` 앞에 정책을 넣는다. 일반 세션의 연결 범위는 바꾸지 않는다. 인증은 Codex 자체 OAuth 저장소를 사용한다. Claude 인증과 Stripe CLI 키를 읽거나 복사하지 않는다. 두 저장소 PR만 열고 설치·머지·로그인은 수행하지 않는다.

**되돌리기:** 두 PR을 되돌리면 기존 Amplitude 전용 동작으로 돌아간다. 전역 설정은 수정하지 않으므로 별도 복구가 필요 없다.

### Task 1: 실행 정책과 회귀 검증

1. 새 확인 세션과 다시 실행한 세션, full 모드, 잘못된 설정, `--`와 호출자 재정의, 일반 역할 제외를 시험한다. 실패를 먼저 확인한다.
2. 공식 문서에 나온 읽기 도구만 허용한다. 새 도구는 기본 차단한다. 외부 Seatbelt 정책을 바꾸지 않는다.
3. 실제 Codex CLI의 임시 HOME에서 설정 해석을 확인한다. macOS에서 직접 쓰기·셸 자식 쓰기를 거부하고 네트워크를 허용하는 시험을 실행한다.
4. 두 저장소 설명을 갱신한다. 세션맥에서 서비스별 Codex OAuth를 별도로 연결하는 명령과 실제 데이터 조회가 아직 검증되지 않았음을 명시한다.
5. 로컬 검증과 자체 코드 검토 후 두 저장소를 각각 커밋하고 한 번씩 push하여 PR을 연다. 자동 독립 확인은 리드가 받는다.

**검증:** `uv run --no-project python3 tools/agent-hooks/codex-startup.test.py`; pane-spawn 관련 shell 시험; 관련 시작 설정 unittest; 실제 Codex MCP 설정 fixture. 모두 종료 코드 0을 기대한다.

**검토 초점:** 문맥 fallback이 정책을 없애는지, CLI 설정 순서가 허용 목록을 넓히는지 확인한다. 비밀값의 출력·공개 저장소 유입과 OAuth 연결 완료를 기능 검증으로 오해하는지도 확인한다.

**근거:** [Codex MCP](https://developers.openai.com/codex/mcp/), [Honeycomb 읽기·쓰기 도구](https://docs.honeycomb.io/integrations/mcp/tools), [Stripe MCP 도구](https://docs.stripe.com/mcp).
