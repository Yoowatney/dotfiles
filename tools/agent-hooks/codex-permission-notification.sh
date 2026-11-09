#!/usr/bin/env bash
# Codex PermissionRequest hook: show the same Hammerspoon input notification as Claude.

log_file="${HOME}/.codex/hooks/permission-notification-debug.log"
mkdir -p "$(dirname "$log_file")"

payload="$(cat 2>/dev/null || true)"

append_log() {
  printf '%s\n' "$*" 2>/dev/null >> "$log_file" || true
}

# The hidden workers herdr server: 요유 never enters those panes, and workers-watch tells the main pane what needs it.
case "${HERDR_SOCKET_PATH:-}" in */sessions/workers/herdr.sock) append_log "SKIPPED workers pane ${HERDR_PANE_ID:-}"; exit 0 ;; esac

# An input/permission request is ordinary status, not an error warning.
append_log "SKIPPED status notification ${HERDR_PANE_ID:-}"
exit 0

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

message="$(printf '%s' "$payload" | jq -r '.message // empty' 2>/dev/null || true)"
tool_name="$(printf '%s' "$payload" | jq -r '.tool_name // .toolName // empty' 2>/dev/null || true)"

if [ -z "$message" ] && [ -n "$tool_name" ]; then
  message="Codex needs your permission to use ${tool_name}"
fi

if [ -z "$message" ]; then
  message="Codex needs your permission"
fi

if [ "${#message}" -gt 80 ]; then
  message="${message:0:77}..."
fi

append_log "MESSAGE: ${message}"
append_log "FINAL MESSAGE: ${message}"
append_log ""

hs="${NOTIFY_HS_BIN:-/opt/homebrew/bin/hs}"
if [ -x "$hs" ]; then
  escaped_message="$(printf '%s' "$message" | sed "s/'/\\\\'/g")"
  "$hs" -c "claudeNotifyInput('$escaped_message', '$session', '$window', '$pane')" >/dev/null 2>&1 &
fi

exit 0
