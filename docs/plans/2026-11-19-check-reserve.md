# 확인 세션: Claude 사용량이 여유분을 넘으면 열지 않기

- 크기: M (`tools/agent-hooks/agent-quota`, `tools/agent-hooks/pane-spawn`, 시험 2개, 이 문서와 `docs/worker-quota-routing.md`)
- 결정: 리드가 2026-11-19에 B안을 골랐다. 확인 세션은 늘 Claude로 열고 Codex는 대신 쓰지 않는다.

## 목표

- 측정된 Claude 사용량이 여유분을 넘으면 확인 세션을 열지 않는다. 여유분은 같은 계정을 쓰는 메인 세션의 몫이다.
- 그때 보고는 이유를 적어 확인 없이 메인 세션에 간다. auto-tab-close의 기존 "확인 칸을 열지 못했습니다" 경로를 그대로 쓴다.
- 사용량을 읽지 못하면(관측 없음, 오래됨, 계정·기기 불일치, 6초 초과) 확인 세션을 Claude로 연다.

## 하지 않는 것

- 확인 세션을 Codex로 여는 대체 경로는 만들지 않는다.
- auto-tab-close와 automatic-check-start는 바꾸지 않는다. 실패한 시작을 한 번 더 시도하는 동작도 그대로 둔다.

## 방법

1. `agent-quota claude-reserve [cwd]`: `claude_quota(first=True)` 결과를 JSON으로 출력한다. 여유분 안이면 0, 측정값이 여유분을 넘었으면 3, 그 밖에는 1로 끝난다.
2. `pane-spawn --check`: 6초 제한 안에서 위 명령을 부른다. 3이면 읽은 값을 적어 exit 2로 끝나고 칸을 열지 않는다. 0이면 그대로 열고, 그 밖의 값이면 "usage unknown"을 stderr에 적고 연다.
3. 여유분 기준은 claude-first 정책과 같다(주간 80% 이하, 5시간 창이 있으면 80% 미만). 이 기준을 고른 것은 구현한 세션의 판단이다.

## 검증

- `test_agent_quota.py`: 여유분 안 0, 주간 80.5% 3, 5시간 80% 3, 추가 결제 계정 3, 관측 없음 1.
- `pane-spawn-quota.test.sh`: 주간 85%와 90%에서 확인 세션이 열리지 않고 herdr를 부르지 않는다. 관측이 없으면 Claude로 열린다. 조회가 1초 제한을 넘기면 30초 안에 Claude로 열린다.
