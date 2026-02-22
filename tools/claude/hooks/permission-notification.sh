#!/bin/bash
# Claude Code Notification Hook - 입력 대기/권한 요청 알림 (Hammerspoon)

LOG_FILE="$HOME/.claude/hooks/permission-notification-debug.log"

# stdin에서 hook input 읽기
HOOK_INPUT=$(cat)

echo "=== $(date) ===" >> "$LOG_FILE"

# tmux 세션/윈도우 정보 (알림에 직접 전달)
SESSION=""
WINDOW=""
if [ -n "$TMUX" ]; then
    SESSION=$(tmux display-message -p '#S')
    if [ -n "$TMUX_PANE" ]; then
        WINDOW=$(tmux display-message -p -t "$TMUX_PANE" '#{window_index}')
    else
        WINDOW=$(tmux display-message -p '#I')
    fi
    echo "TMUX CONTEXT: ${SESSION}:${WINDOW} (PANE=${TMUX_PANE})" >> "$LOG_FILE"
fi

# message 추출
MESSAGE=$(echo "$HOOK_INPUT" | jq -r '.message // empty')
echo "MESSAGE: $MESSAGE" >> "$LOG_FILE"

# 기본 메시지
if [ -z "$MESSAGE" ]; then
    MESSAGE="입력이 필요합니다"
fi

# 80자 제한
if [ ${#MESSAGE} -gt 80 ]; then
    MESSAGE="${MESSAGE:0:77}..."
fi

echo "FINAL MESSAGE: $MESSAGE" >> "$LOG_FILE"
echo "" >> "$LOG_FILE"

# Hammerspoon으로 알림 (클릭 시 tmux 세션 이동)
ESCAPED_MESSAGE=$(echo "$MESSAGE" | sed "s/'/\\\\'/g")
/opt/homebrew/bin/hs -c "claudeNotifyInput('$ESCAPED_MESSAGE', '$SESSION', '$WINDOW')" &

exit 0
