#!/usr/bin/env bash
# 공개 저장소에 둘 수 없는 Claude 전역 설정을 다른 맥북으로 보낸다.
#
# 이 저장소는 공개다. ~/.claude/CLAUDE.md 는 회사 이메일과 Slack 계정을,
# settings.json 의 autoMode.environment 는 GCP 프로젝트와 시크릿 이름, Discord
# 채널, 프로덕션 DB 컬렉션을 담고 있어서 파일 자체를 여기에 커밋하지 않는다.
# 같은 이유로 llm-wiki 프롬프트를 빼낸 커밋 7e2b034 를 참고할 것.
#
# 회사 정보가 없는 나머지 전역 설정 — commands, output-styles, keybindings —
# 은 init.sh 가 이 저장소에서 심볼릭 링크로 걸어주므로 여기서 다루지 않는다.
#
# 먼저 -n 을 붙여 무엇이 갈지 확인하는 편이 안전하다.
set -euo pipefail

if [[ $# -lt 1 ]]; then
    echo "usage: $(basename "$0") <ssh-target> [rsync args...]" >&2
    echo "  예: $(basename "$0") yoyoo@my-server -n   # 무엇이 갈지 확인만" >&2
    echo "      $(basename "$0") yoyoo@my-server      # 실제 전송" >&2
    exit 1
fi

target="$1"
shift

files=("$HOME/.claude/CLAUDE.md" "$HOME/.claude/settings.json")

dry=0
for arg in "$@"; do
    case "$arg" in -n|--dry-run) dry=1 ;; esac
done

# macOS 기본 rsync 는 openrsync 라서 --backup/--suffix 를 구현하지 않는다.
# 조용히 덮어쓰고 끝나므로, 되돌릴 수 있게 받는 쪽에서 먼저 사본을 만든다.
# 확인만 하는 실행(-n)은 아무것도 건드리지 않아야 하므로 건너뛴다.
if (( dry == 0 )); then
    stamp=$(date +%Y%m%d-%H%M%S)
    ssh "$target" "cd ~/.claude || exit 0
        for f in CLAUDE.md settings.json; do
            [ -f \"\$f\" ] && cp -p \"\$f\" \"\$f.bak-$stamp\" && echo \"backed up ~/.claude/\$f.bak-$stamp\"
        done"
fi

rsync -av "$@" "${files[@]}" "$target:.claude/"
