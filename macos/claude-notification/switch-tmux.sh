#!/bin/bash
# 알림 클릭 시 해당 tmux 세션/윈도우/페인으로 이동
# Usage: switch-tmux.sh [session] [window] [pane]

LOG="/tmp/switch-tmux.log"
echo "=== $(date) ===" >> "$LOG"

SESSION="$1"
WINDOW="$2"
PANE="$3"

echo "SESSION=$SESSION, WINDOW=$WINDOW, PANE=$PANE" >> "$LOG"

if [ -z "$SESSION" ]; then
    osascript -e 'tell application "iTerm" to activate'
    echo "세션 정보 없음, iTerm만 활성화" >> "$LOG"
    exit 0
fi

TMUX=/opt/homebrew/bin/tmux

# 해당 세션에 붙어있는 tmux 클라이언트의 TTY 찾기
TTY=$($TMUX list-clients -t "$SESSION" -F '#{client_tty}' 2>/dev/null | head -1)
echo "TTY=$TTY" >> "$LOG"

# tmux 윈도우 선택
if [ -n "$WINDOW" ]; then
    $TMUX select-window -t "$SESSION:$WINDOW" 2>/dev/null
fi

# pane 선택 + zoom
if [ -n "$PANE" ]; then
    # 다른 pane이 zoom 되어있으면 먼저 해제
    IS_ZOOMED=$($TMUX display-message -p -t "$SESSION:$WINDOW" '#{window_zoomed_flag}' 2>/dev/null)
    if [ "$IS_ZOOMED" = "1" ]; then
        $TMUX resize-pane -Z -t "$SESSION:$WINDOW" 2>/dev/null
    fi
    # 해당 pane 선택 후 zoom
    $TMUX select-pane -t "$PANE" 2>/dev/null
    $TMUX resize-pane -Z -t "$PANE" 2>/dev/null
    echo "pane 선택 + zoom: $PANE" >> "$LOG"
fi

if [ -n "$TTY" ]; then
    osascript <<APPLESCRIPT
tell application "iTerm"
    activate
    repeat with w in windows
        repeat with t in tabs of w
            repeat with s in sessions of t
                if tty of s is "$TTY" then
                    select w
                    return
                end if
            end repeat
        end repeat
    end repeat
end tell
APPLESCRIPT
    echo "iTerm 윈도우 전환 완료 (TTY: $TTY)" >> "$LOG"
else
    osascript -e 'tell application "iTerm" to activate'
    echo "클라이언트 없음, iTerm만 활성화" >> "$LOG"
fi
