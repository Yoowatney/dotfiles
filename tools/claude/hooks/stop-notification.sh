#!/bin/bash
# Claude Code Stop Hook - 작업 완료 알림 (Hammerspoon)

LOG_FILE="$HOME/.claude/hooks/stop-hook-debug.log"

# stdin에서 hook input 읽기
HOOK_INPUT=$(cat)

echo "=== $(date) ===" >> "$LOG_FILE"

# tmux 세션/윈도우 정보 (알림에 직접 전달)
SESSION=""
WINDOW=""
if [ -n "$TMUX" ]; then
    SESSION=$(tmux display-message -p '#S')
    # TMUX_PANE으로 실제 프로세스가 돌고 있는 윈도우를 찾음
    # (display-message -p '#I'는 현재 보고 있는 윈도우를 반환하므로 부정확)
    if [ -n "$TMUX_PANE" ]; then
        WINDOW=$(tmux display-message -p -t "$TMUX_PANE" '#{window_index}')
    else
        WINDOW=$(tmux display-message -p '#I')
    fi
    PANE="$TMUX_PANE"
    echo "TMUX CONTEXT: ${SESSION}:${WINDOW} (PANE=${PANE})" >> "$LOG_FILE"
fi

# transcript_path 추출
TRANSCRIPT_PATH=$(echo "$HOOK_INPUT" | jq -r '.transcript_path // empty')
echo "TRANSCRIPT_PATH: $TRANSCRIPT_PATH" >> "$LOG_FILE"

# 기본 메시지
MESSAGE="작업이 완료되었습니다"

# transcript 파일이 있으면 마지막 응답에서 요약 추출
if [ -n "$TRANSCRIPT_PATH" ] && [ -f "$TRANSCRIPT_PATH" ]; then
    SUMMARY=$(tail -r "$TRANSCRIPT_PATH" 2>/dev/null | \
        jq -r 'select(.type == "assistant" and .message.type == "message") | .message.content[] | select(.type == "text") | .text // empty' 2>/dev/null | \
        head -1 | \
        sed 's/^[[:space:]]*//' | \
        tr '\n' ' ' | \
        cut -c1-80)

    echo "SUMMARY: $SUMMARY" >> "$LOG_FILE"

    if [ -n "$SUMMARY" ]; then
        MESSAGE="$SUMMARY"
        ORIGINAL_LEN=$(tail -r "$TRANSCRIPT_PATH" 2>/dev/null | \
            jq -r 'select(.type == "assistant" and .message.type == "message") | .message.content[] | select(.type == "text") | .text // empty' 2>/dev/null | \
            head -1 | wc -c)
        if [ "$ORIGINAL_LEN" -gt 80 ]; then
            MESSAGE="${MESSAGE}..."
        fi
    fi
fi

echo "FINAL MESSAGE: $MESSAGE" >> "$LOG_FILE"
echo "" >> "$LOG_FILE"

# Hammerspoon으로 알림 (클릭 시 tmux 세션 이동)
# 메시지에서 작은따옴표 이스케이프
ESCAPED_MESSAGE=$(echo "$MESSAGE" | sed "s/'/\\\\'/g")
/opt/homebrew/bin/hs -c "claudeNotifyDone('$ESCAPED_MESSAGE', '$SESSION', '$WINDOW', '$PANE')" &

exit 0
