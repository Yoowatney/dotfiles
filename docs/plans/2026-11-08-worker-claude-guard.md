# 부하 칸의 Claude 확인 요청 차단 계획

**Goal:** 부하 칸이 Bash에서 직접 Claude 모델 요청을 보내는 일을 막고, 요청을 전송하지 않는 probe로 안내한다.

**Architecture:** 두 도구가 이미 등록한 Bash 훅을 늘린다. `herdr_peers`의 서버·시작자·메인 칸 판별을 재사용한다. 훅 밖의 launchd와 정상 pane-spawn은 이 검사 대상이 아니다.

**Tech Stack:** Python 표준 라이브러리, 기존 shell parser, herdr JSON.

**Constraints:** 실제 모델 요청을 보내지 않는다. 다른 작업의 파일을 바꾸지 않는다. 공개 diff에 개인 경로와 세션 값을 넣지 않는다. 판별 실패는 기존 동작을 유지한다.

**Review focus:** 메인·수동 세션 오차단, 프롬프트·파이프·shell wrapper 누락, 실제 실행 없이 시험하는지.

- [ ] 독립적인 herdr 대역과 새 훅 프로세스에 JSON을 넣어 차단·허용 시험 작성 → 기존 코드에서 차단 시험 실패 확인.
- [ ] 기존 Bash 훅에 명령 및 부하 칸 판별 추가 → 새 시험과 기존 selftest 통과.
- [ ] Claude·Codex 등록 경로를 확인하고 새 프로세스에서 대표 명령의 판정 저장 → 실제 Claude 실행 없음.
- [ ] 독립 검토, 공개 diff 점검, PR·검사·머지 → 검사 결과와 되돌리는 법 보고.
