#!/bin/bash

# Claude Code SessionStart hook - direnv + mise 환경변수 로드

# 디버깅 로그
LOG_FILE="$HOME/.claude/hooks/session-env.log"
echo "[$(date)] Hook started, CLAUDE_ENV_FILE=$CLAUDE_ENV_FILE" >> "$LOG_FILE"

# tmux-resurrect integration: tag this pane with the Claude session id so
# resurrect-claude-resume.sh can rewrite saved commands to `claude --resume <id>`
if [ -n "$TMUX_PANE" ]; then
  command -v jq >/dev/null || PATH="/opt/homebrew/bin:$PATH"
  SESSION_ID=$(jq -r '.session_id // empty' 2>/dev/null)
  if [ -n "$SESSION_ID" ]; then
    tmux set-option -pt "$TMUX_PANE" @claude_session_id "$SESSION_ID" 2>/dev/null
  fi
fi

if [ -n "$CLAUDE_ENV_FILE" ]; then
  # direnv export로 환경변수 가져오기
  direnv export bash 2>/dev/null | grep '^export ' >> "$CLAUDE_ENV_FILE"

  # mise 도구 경로를 기존 PATH 앞에 추가 (PATH 덮어쓰기 방지)
  MISE_PATHS=$(mise env 2>/dev/null | grep '^export PATH=' | sed "s/export PATH='//" | sed "s/'$//" | tr ':' '\n' | grep mise | tr '\n' ':' | sed 's/:$//')
  if [ -n "$MISE_PATHS" ]; then
    echo "export PATH=\"$MISE_PATHS:\$PATH\"" >> "$CLAUDE_ENV_FILE"
  fi
fi

exit 0
