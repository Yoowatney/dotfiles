#!/usr/bin/env bash
# The turn-end and input-needed desktop alerts of Claude and Codex: a pane on the hidden workers herdr server stays silent,
# every other pane keeps its alert. A fake hs records the calls (NOTIFY_HS_BIN).
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
fails=0
check() { if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: expected [$3] got [$2]"; fails=1; fi; }

printf '#!/bin/sh\nprintf "%%s\\n" "$2" >> "%s/hs.log"\n' "$SANDBOX" > "$SANDBOX/hs"
chmod +x "$SANDBOX/hs"
mkdir -p "$SANDBOX/.claude/hooks"

WORKERS="$HOME/.config/herdr/sessions/workers/herdr.sock"
HOOKS=(
  "$HERE/../claude/hooks/stop-notification.sh"
  "$HERE/../claude/hooks/permission-notification.sh"
  "$HERE/codex-stop-notification.sh"
  "$HERE/codex-permission-notification.sh"
)
# <hook> <socket, empty for none>: prints how many alerts went out
alerts() {
  rm -f "$SANDBOX/hs.log"
  local e=(env -u TMUX -u TMUX_PANE -u HERDR_SOCKET_PATH HOME="$SANDBOX" NOTIFY_HS_BIN="$SANDBOX/hs" HERDR_PANE_ID=w1:p7)
  [ -z "$2" ] || e+=(HERDR_SOCKET_PATH="$2")
  printf '{"transcript_path":"","last_assistant_message":"끝","message":"입력 필요"}' | "${e[@]}" bash "$1" >/dev/null 2>&1
  # the hooks start hs in the background
  local i; for i in 1 2 3 4 5 6; do [ ! -s "$SANDBOX/hs.log" ] || break; sleep 0.05; done
  [ -f "$SANDBOX/hs.log" ] && wc -l < "$SANDBOX/hs.log" | tr -d ' ' || echo 0
}
for hook in "${HOOKS[@]}"; do
  n=$(basename "$hook")
  [ -f "$hook" ] || { echo "FAIL $n: no such hook"; fails=1; continue; }
  check "$n: a workers pane sends no alert" "$(alerts "$hook" "$WORKERS")" 0
  check "$n: the skipped pane is logged" "$(cat "$SANDBOX"/.claude/hooks/*-debug.log "$SANDBOX"/.codex/hooks/*-debug.log 2>/dev/null | grep -c '^SKIPPED workers pane w1:p7$')" 1
  rm -rf "$SANDBOX/.claude/hooks"/*-debug.log "$SANDBOX/.codex/hooks"/*-debug.log
  check "$n: a default pane keeps its alert" "$(alerts "$hook" "$HOME/.config/herdr/herdr.sock")" 1
  check "$n: another named server keeps its alert" "$(alerts "$hook" "$HOME/.config/herdr/sessions/resume/herdr.sock")" 1
  check "$n: outside herdr keeps its alert" "$(alerts "$hook" "")" 1
done
exit $fails
