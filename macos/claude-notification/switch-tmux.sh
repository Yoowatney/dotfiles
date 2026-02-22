#!/bin/bash
# 알림 클릭 시 해당 tmux 세션/윈도우의 iTerm 윈도우로 이동
# Usage: switch-tmux.sh [session] [window]

LOG="/tmp/switch-tmux.log"
echo "=== $(date) ===" >> "$LOG"

SESSION="$1"
WINDOW="$2"

echo "SESSION=$SESSION, WINDOW=$WINDOW" >> "$LOG"

if [ -z "$SESSION" ]; then
    osascript -e 'tell application "iTerm" to activate'
    echo "세션 정보 없음, iTerm만 활성화" >> "$LOG"
    exit 0
fi

# 해당 세션에 붙어있는 tmux 클라이언트의 TTY 찾기
TTY=$(/opt/homebrew/bin/tmux list-clients -t "$SESSION" -F '#{client_tty}' 2>/dev/null | head -1)
echo "TTY=$TTY" >> "$LOG"

# tmux 윈도우 먼저 선택
if [ -n "$WINDOW" ]; then
    /opt/homebrew/bin/tmux select-window -t "$SESSION:$WINDOW" 2>/dev/null
fi

if [ -n "$TTY" ]; then
    # TTY에 해당하는 iTerm 윈도우/탭을 찾아서 활성화
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
    # 클라이언트 못 찾으면 iTerm만 활성화
    osascript -e 'tell application "iTerm" to activate'
    echo "클라이언트 없음, iTerm만 활성화" >> "$LOG"
fi
