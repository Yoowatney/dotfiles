#!/bin/bash

# Claude Code SessionStart hook - direnv + mise 환경변수 로드

# 디버깅 로그
LOG_FILE="$HOME/.claude/hooks/session-env.log"
echo "[$(date)] Hook started, CLAUDE_ENV_FILE=$CLAUDE_ENV_FILE" >> "$LOG_FILE"

# tmux-resurrect integration: tag this pane with the Claude session id, its
# transcript path and its startup cwd, so resurrect-claude-resume.sh can rewrite
# saved commands to `claude --resume <id>` *and* restore the pane in the cwd that
# `--resume` needs (session lookup is scoped to ~/.claude/projects/<mangled cwd>).
if [ -n "$TMUX_PANE" ]; then
  command -v jq >/dev/null || PATH="/opt/homebrew/bin:$PATH"
  PAYLOAD=$(cat)  # stdin is readable once — parse everything from this copy
  SESSION_ID=$(printf '%s' "$PAYLOAD" | jq -r '.session_id // empty' 2>/dev/null)
  TRANSCRIPT=$(printf '%s' "$PAYLOAD" | jq -r '.transcript_path // empty' 2>/dev/null)
  SESSION_CWD=$(printf '%s' "$PAYLOAD" | jq -r '.cwd // empty' 2>/dev/null)
  echo "  tags: id=${SESSION_ID:-none} transcript=${TRANSCRIPT:-none} cwd=${SESSION_CWD:-none}" >> "$LOG_FILE"
  if [ -n "$SESSION_ID" ]; then
    tmux set-option -pt "$TMUX_PANE" @claude_session_id "$SESSION_ID" 2>/dev/null
    tmux set-option -pt "$TMUX_PANE" @claude_transcript "$TRANSCRIPT" 2>/dev/null
    tmux set-option -pt "$TMUX_PANE" @claude_cwd "$SESSION_CWD" 2>/dev/null
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
