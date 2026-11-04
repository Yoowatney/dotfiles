#!/usr/bin/env bash
# Codex Stop hook: show desktop notification and keep a small debug trail.

log_file="${HOME}/.codex/hooks/stop-hook-debug.log"
mkdir -p "$(dirname "$log_file")"

payload="$(cat 2>/dev/null || true)"

append_log() {
  printf '%s\n' "$*" 2>/dev/null >> "$log_file" || true
}

# The hidden workers herdr server: 요유 never enters those panes, and workers-watch tells the main pane what needs it.
case "${HERDR_SOCKET_PATH:-}" in */sessions/workers/herdr.sock) append_log "SKIPPED workers pane ${HERDR_PANE_ID:-}"; exit 0 ;; esac

append_log "=== $(date) ==="

session=""
window=""
pane="${TMUX_PANE:-}"

if [ -n "${TMUX:-}" ]; then
  session="$(tmux display-message -p '#S' 2>/dev/null || true)"
  if [ -n "$pane" ]; then
    window="$(tmux display-message -p -t "$pane" '#{window_index}' 2>/dev/null || true)"
  else
    window="$(tmux display-message -p '#I' 2>/dev/null || true)"
  fi
  append_log "TMUX CONTEXT: ${session}:${window} (PANE=${pane})"
fi

transcript_path="$(printf '%s' "$payload" | jq -r '.transcript_path // empty' 2>/dev/null || true)"
last_message="$(printf '%s' "$payload" | jq -r '.last_assistant_message // empty' 2>/dev/null || true)"
append_log "TRANSCRIPT_PATH: ${transcript_path}"

message="작업이 완료되었습니다"

if [ -n "$last_message" ]; then
  message="$(printf '%s' "$last_message" | sed 's/^[[:space:]]*//' | tr '\n' ' ' | cut -c1-80)"
elif [ -n "$transcript_path" ] && [ -f "$transcript_path" ]; then
  summary="$(
    tail -r "$transcript_path" 2>/dev/null \
      | jq -r '
          if .type == "response_item" and .payload.type == "message" and .payload.role == "assistant" then
            .payload.content[]? | select(.type == "output_text" or .type == "text") | .text // empty
          elif .type == "assistant" and .message.type == "message" then
            .message.content[]? | select(.type == "text") | .text // empty
          else
            empty
          end
        ' 2>/dev/null \
      | head -1 \
      | sed 's/^[[:space:]]*//' \
      | tr '\n' ' ' \
      | cut -c1-80
  )"
  if [ -n "$summary" ]; then
    message="$summary"
  fi
fi

append_log "FINAL MESSAGE: ${message}"
append_log ""

hs="${NOTIFY_HS_BIN:-/opt/homebrew/bin/hs}"
if [ -x "$hs" ]; then
  escaped_message="$(printf '%s' "$message" | sed "s/'/\\\\'/g")"
  "$hs" -c "claudeNotifyDone('$escaped_message', '$session', '$window', '$pane')" >/dev/null 2>&1 &
fi

exit 0
