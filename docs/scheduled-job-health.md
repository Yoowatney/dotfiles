# 정기 작업의 연속 실패 알림

정기 실행 명령의 래퍼가 실행 결과를 잡별로 기록한다. 실패와 건너뜀을 합쳐 두 번 연속이면 기존 메인 알림함에 알림 하나를 넣는다. 같은 장애가 이어지는 동안에는 추가 알림을 만들지 않는다. 성공하면 연속 횟수를 초기화한다. 실패 한 번의 종료 코드와 기존 알림 동작은 유지한다.

알림은 두 번째 결과를 저장한 직후 넣는다. 알림함이 잠겨 있거나 쓰기에 실패하면 미전달 알림을 보존하고 다음 명령 실행 전에 재시도한다. 기존 알림함이 메인 세션에 내용을 제공하며, 읽음 확인은 기존 절차를 따른다. 실행 래퍼가 강제 종료되면 다음 시작 시 미완료 시도를 한 번 실패로 센다. 설치 전의 과거 결과는 추정하지 않는다.

## 판정 근거

- 원래 프로세스가 0이 아닌 코드로 끝나면 실패다.
- 잡이 `SCHEDULED_JOB_OUTCOME_FILE`에 `failed` 또는 `skipped`를 쓰면 그 결과를 사용한다. `main-refresh`는 이 경로를 지원한다. 이 파일에는 결과 단어만 쓰고 로그나 원문을 복사하지 않는다.
- 알려진 실행 스크립트는 이번 실행이 추가한 로그에서 잡 전체의 건너뜀이나 실패를 판정한다. 개별 항목을 생략했거나 변경할 내용이 없는 정상 실행은 실패가 아니다. 동기화 로그의 마지막 `ok`는 앞선 선택 단계의 생략을 성공으로 마무리한다.
- 종료 0과 기록만으로 성공·건너뜀을 구분할 수 없는 경우는 추측하지 않는다. 원격에 붙지 못하면 기록 없이 종료 0으로 끝나는 잡 하나는 이번 건너뜀 감시 범위에서 제외한다. 일정 자체가 실행되지 않은 횟수도 추정하지 않는다.

## 목록 확인과 설치

macOS 기본 Python을 사용한다. 다음 예시의 `example.`은 설치할 사용자의 잡 접두어로 바꾼다. 접두어는 여러 번 줄 수 있다. 기본 실행은 읽기 전용이며, 일정·종료 코드·로그 경로와 제외 이유를 JSON으로 출력한다. 명령의 인자 값은 출력하지 않는다.

```sh
/usr/bin/python3 tools/agent-hooks/install-scheduled-job-health.py --prefix example.
```

목록을 검토한 뒤 적용한다. 적용 도구는 plist만 바꾸며 launchctl을 호출하지 않는다. 일정·환경·출력 경로를 보존하고, 원래 파일과 심볼릭 링크를 비공개 상태 디렉터리에 백업한다. 반복 적용은 중첩 래퍼를 만들지 않는다. KeepAlive 서비스, 비정기 잡, 비활성 plist, `Program`을 별도로 지정한 잡은 자동으로 바꾸지 않는다. launchctl의 별도 비활성 설정은 이 도구가 조회하거나 변경하지 않으므로, 재등록할 대상은 운영자가 확인한다.

```sh
/usr/bin/python3 tools/agent-hooks/install-scheduled-job-health.py --prefix example. --apply > /tmp/job-health-install.json
/usr/bin/python3 - <<'PY' > /tmp/job-health-reload.sh
import json
print('set -e')
for command in json.load(open('/tmp/job-health-install.json'))['reload_commands']:
    print(command + (' || true' if ' bootout ' in command else ''))
PY
bash /tmp/job-health-reload.sh
```

출력된 재등록 명령은 실행 중인 잡을 중단할 수 있다. 해당 잡이 쉬는 때 실행한다. `RunAtLoad`가 있는 잡은 재등록 직후 실행될 수 있다. 임의의 `kickstart`는 필요하지 않다. 설치 도구와 래퍼는 계속 존재할 최종 체크아웃에서 실행한다. 저장한 명령은 현재 체크아웃 경로를 참조하므로 임시 worktree를 설치 경로로 쓰지 않는다.

도구가 만든 plist를 다른 설치 스크립트가 덮어쓰면 감시도 빠진다. 도구는 변경된 plist를 자동으로 덮어쓰거나 복구하지 않는다. 원래 설치 스크립트를 실행하기 전 아래 복구 절차로 래퍼를 제거하고, 설치 후 목록을 다시 검토해 적용한다.

## 복구

```sh
/usr/bin/python3 tools/agent-hooks/install-scheduled-job-health.py --prefix example. --restore > /tmp/job-health-restore.json
```

위와 같은 방법으로 `job-health-restore.json`의 `reload_commands`를 실행한다. 원래 plist 내용·파일 권한·심볼릭 링크를 복구한다. 실행 결과와 알림 이력은 지우지 않는다. 설치 이후 plist가 변경됐으면 덮어쓰지 않고 중단한다. 잡의 정상 결과가 기록되기 전까지 같은 장애의 중복 방지는 유지된다.

## 로컬 시험

```sh
/usr/bin/python3 tools/agent-hooks/scheduled-job-health.test.py
/usr/bin/python3 tools/agent-hooks/install-scheduled-job-health.test.py
/usr/bin/python3 tools/agent-hooks/main-alert-inbox.test.py
bash tools/agent-hooks/main-refresh.test.sh
/usr/bin/python3 tools/agent-hooks/main-refresh-race.test.py
```

시험은 임시 상태 디렉터리와 자식 명령을 사용하며 실제 LaunchAgents를 설치하거나 launchctl을 호출하지 않는다.
