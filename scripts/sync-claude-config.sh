#!/usr/bin/env bash
# 공개 저장소에 둘 수 없는 Claude 전역 설정을 다른 맥북으로 보낸다.
#
# 이 저장소는 공개다. ~/.claude/CLAUDE.md 는 회사 이메일과 Slack 계정을,
# settings.json 의 autoMode.environment 는 GCP 프로젝트와 시크릿 이름, Discord
# 채널, 프로덕션 DB 컬렉션을 담고 있어서 파일 자체를 여기에 커밋하지 않는다.
# (같은 이유로 llm-wiki 프롬프트를 빼낸 커밋 7e2b034 를 참고할 것.)
#
# 회사 정보가 없는 나머지 전역 설정 — commands, output-styles, keybindings —
# 은 init.sh 가 이 저장소에서 심볼릭 링크로 걸어주므로 여기서 다루지 않는다.
#
# 덮어쓰기 전에 수신 측이 원본을 .bak-<날짜> 로 남기므로, 잘못 보냈을 때
# 되돌릴 수 있다. 먼저 -n 을 붙여 무엇이 갈지 확인하는 편이 안전하다.
set -euo pipefail

if [[ $# -lt 1 ]]; then
    echo "usage: $(basename "$0") <ssh-target> [rsync args...]" >&2
    echo "  예: $(basename "$0") yoyoo@my-server -n   # 먼저 확인만" >&2
    echo "      $(basename "$0") yoyoo@my-server      # 실제 전송" >&2
    exit 1
fi

target="$1"
shift

rsync -av --backup --suffix=".bak-$(date +%Y%m%d-%H%M%S)" "$@" \
    "$HOME/.claude/CLAUDE.md" \
    "$HOME/.claude/settings.json" \
    "$target:.claude/"
