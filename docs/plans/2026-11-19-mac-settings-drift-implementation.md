# 맥 설정 차이 매일 점검 구현 계획

> 실행: superpowers:executing-plans (Claude 세션 안에서 직접 구현). 단계는 체크박스로 추적한다.

**목표:** 세 맥의 설정이 dotfiles와 다르면 세션맥이 매일 메인 알림함에 알린다.

**구조:** 맥마다 `verify.sh`가 새 검사를 실행하고, 실패한 검사 이름을 `VERIFY-FAIL <이름>` 줄로 출력한다. 세션맥의 `settings-drift.py`가 세 맥에서 `verify.sh`를 실행해 그 줄을 모으고, 새 실패만 알린다.

**기술:** bash, `/usr/bin/python3`(표준 라이브러리), Swift(Carbon TIS) 하나.

**설계:** `docs/plans/2026-11-19-mac-settings-drift-design.md`

## 전체 제약

- 저장소에 주소·회사 정보를 넣지 않는다. 점검 대상은 `~/.config/herdr/remote-host`, `~/.config/munice/portable-host`의 첫 줄에서 읽는다.
- 알림과 상태 파일에는 맥 이름과 검사 이름만 넣는다. 설정값과 출력 원문은 넣지 않는다.
- 점검은 설정을 바꾸지 않는다. `apply`는 `init.sh`와 사람이 직접 실행할 때만 쓴다.
- 접속에 실패한 맥(ssh 종료 코드 255)은 그날 건너뛴다.

## 검토 초점

- 입력 소스를 켠 직후 선택이 `-50`으로 실패한다(2026-11-19 휴대맥). `apply`는 최대 10번, 0.5초 간격으로 다시 선택한다.
- `verify.sh`가 중간에 끝나서 `VERIFY-DONE`을 출력하지 않으면, 실패가 0개로 보이면 안 된다. `점검 실행 실패`로 처리한다.
- 같은 실패가 이어지면 알림이 다시 생기면 안 된다. 실패가 해결된 뒤 다시 생기면 새로 알린다.
- 알림함 쓰기에 실패하면 상태를 저장하지 않는다. 그래야 다음 실행 때 다시 알린다.
- `defaults.sh`의 값이 `$`를 포함하면 검사하지 않는다(실행할 때 정해지는 값).

---

### 작업 1: 입력 소스 도구

**파일**
- 만들기: `macos/input-sources/input-sources.swift`, `macos/input-sources/enabled.txt`, `scripts/input-sources.sh`, `scripts/input-sources.test.sh`
- 고치기: `init.sh` (post_install의 구름 입력기 부분과 마지막 안내 문구)
- 지우기: `scripts/setup-gureum.sh`

**인터페이스**
- `scripts/input-sources.sh <list|check|apply> [enabled-file]`: 바이너리가 없거나 소스보다 오래됐으면 `~/.cache/dotfiles/input-sources`로 컴파일한 뒤 실행한다. 기본 목록 파일은 `macos/input-sources/enabled.txt`.
- `check`: 차이가 없으면 0, 있으면 `missing <id>`, `extra <id>` 줄을 출력하고 1로 끝난다. 키보드 입력 소스 중 선택할 수 있는 것만 비교한다.
- `apply`: 등록 → 목록의 입력 소스를 켬 → 첫 줄을 선택(재시도) → 목록에 없는 키보드 입력 소스를 끔.

- [ ] 실패 시험을 쓴다: `scripts/input-sources.test.sh`가 `com.apple.keylayout.ABC` 한 줄짜리 임시 목록으로 `check`를 실행하면 1로 끝나고 `missing com.apple.keylayout.ABC`를 출력한다. 실제 `enabled.txt`로 실행하면 0으로 끝난다(구름 입력기를 설정한 맥에서).
- [ ] 시험이 실패하는지 본다: `bash scripts/input-sources.test.sh` → 스크립트가 없어서 실패.
- [ ] 도구와 스크립트를 만든다. `init.sh`는 `bash "$DOTFILES/scripts/input-sources.sh" apply || warn ...`로 바꾼다.
- [ ] 시험을 통과시킨다.
- [ ] 커밋한다.

### 작업 2: `verify.sh` 새 검사와 실패 이름 출력

**파일**
- 만들기: `macos/settings-check.py`, `macos/settings-check.test.py`
- 고치기: `verify.sh`, `macos/snap/snap.xml`(`apps` 키), `macos/dock/dock.xml`(`persistent-apps` 키)

**인터페이스**
- `settings-check.py`는 검사마다 `ok<TAB><이름>` 또는 `fail<TAB><이름>` 한 줄을 출력한다. 이름 형식: `defaults <도메인> <키>`, `hotkey 60 F18`, `brew cask <이름>`, `mas <이름>`, `dock persistent-apps`, `snap apps`.
- 순수 함수: `parse_defaults(text) -> list[tuple[bool, str, str, str, str]]`(currentHost, 도메인, 키, 형식, 값), `same_value(kind, want, have) -> bool`, `parse_brewfile(text) -> tuple[list[str], list[tuple[str, str]]]`(cask 이름들, (mas 이름, id)들), `dock_ids(prefs: dict) -> list[str]`.
- `verify.sh`: `check`와 `check_symlink`가 실패한 이름을 배열에 모은다. 끝에 `VERIFY-FAIL <이름>`을 한 줄씩, 마지막에 `VERIFY-DONE`을 출력한다. 종료 코드는 지금과 같다.

- [ ] 실패 시험을 쓴다(`settings-check.test.py`, unittest):
  - `defaults write com.apple.dock autohide -bool true`, `defaults -currentHost write -g com.apple.mouse.tapBehavior -int 1`이 각각 올바른 튜플이 된다. `-dict-add` 줄과 값에 `$`가 든 줄은 빠진다.
  - `same_value('bool', 'true', '1')`, `same_value('float', '0.5', '0.50')`, `same_value('string', '"png"', 'png')`는 참, `same_value('int', '2', '3')`은 거짓.
  - `cask "aldente"  # 주석`과 `mas "Snap", id: 418073146`을 읽는다.
  - `dock_ids`가 `persistent-apps`의 `tile-data.bundle-identifier`를 순서대로 돌려준다.
- [ ] 시험이 실패하는지 본다: `/usr/bin/python3 macos/settings-check.test.py`.
- [ ] `settings-check.py`를 만든다. Snap은 `apps` 바이트를 그대로 비교하고, Dock은 번들 ID 목록을 비교한다.
- [ ] `verify.sh`에 `🧭 Settings` 구역을 더하고 실패 이름 출력을 넣는다.
- [ ] 세션맥 값으로 두 xml의 해당 키만 바꾼다(`plutil -replace`). 공개해도 되는 값인지 확인한다.
- [ ] 시험과 `bash verify.sh`를 실행해 `VERIFY-DONE`이 나오는지 본다.
- [ ] 커밋한다.

### 작업 3: 세션맥 매일 점검

**파일**
- 만들기: `tools/agent-hooks/settings-drift.py`, `tools/agent-hooks/settings-drift.test.py`, `tools/claude/com.yoyoo.settings-drift.plist`

**인터페이스**
- 사용: 작업 2의 `VERIFY-FAIL`, `VERIFY-DONE` 줄, `main-alert-inbox.py`의 `enqueue(env, event_id, title, body)`.
- 대상: 세션맥 자신, 그리고 두 설정 파일의 첫 줄. 원격 명령은 `scutil --get ComputerName; bash ~/.dotfiles/verify.sh`. 시험용으로 `SETTINGS_DRIFT_SSH`, `SETTINGS_DRIFT_BASH` 환경 변수가 ssh와 bash 경로를 바꾼다.
- 상태: `~/.local/state/settings-drift/state.json`, 대상마다 `{"name", "first_seen", "last_ok", "fails", "stale_alerted"}`.
- 알림 `event_id`: `settings-drift-` + 새 실패 묶음의 sha256 앞 16자 + 날짜.

- [ ] 실패 시험을 쓴다(가짜 ssh와 임시 HOME):
  - 새 실패는 알림 하나가 되고, 같은 실패로 다시 실행하면 알림이 늘지 않는다.
  - ssh가 255로 끝나면 알림이 없고 `last_ok`가 바뀌지 않는다.
  - `VERIFY-DONE`이 없으면 `점검 실행 실패` 알림이 생긴다.
  - `last_ok`가 8일 전이고 접속에 실패하면 알림이 하나 생기고, 다음 실행에서는 늘지 않는다.
  - 알림함 쓰기에 실패하면 종료 코드 1이고 상태가 저장되지 않는다.
- [ ] 시험이 실패하는지 본다: `/usr/bin/python3 tools/agent-hooks/settings-drift.test.py`.
- [ ] `settings-drift.py`와 plist(매일 10:30)를 만든다.
- [ ] 시험을 통과시킨다.
- [ ] 커밋한다.

### 작업 4: 리뷰, PR, 설치, 차이 정리

- [ ] codex-review로 검토하고, 지적을 반영한다.
- [ ] PR을 만든다. 본문의 `## 검증`에 시험 결과를 적는다.
- [ ] 머지한 뒤 세션맥에 잡을 설치한다(`install-scheduled-job-health.py --prefix com.yoyoo.settings-drift`).
- [ ] 휴대맥에 AlDente를 설치하고, 휴대맥과 집맥에 Snap·Dock 값을 적용하고, 세션맥에 `macos/defaults.sh`를 다시 적용한다.
- [ ] 점검을 한 번 실행해서 알림 내용을 확인한다.
