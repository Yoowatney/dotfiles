#!/usr/bin/env bash
# tmux's break-pane / join-pane, which herdr ships as `herdr pane move` but binds
# to no key. Everything below is just argument plumbing for that one command.
#
# ponytail: no state of its own; the server is the source of truth for what is
# focused. Every run appends to the log so a silent detached `type = "shell"`
# binding is still debuggable.
set -euo pipefail

LOG=/tmp/herdr-pane-move.log
log() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*" >>"$LOG"; }

cur=$(herdr pane current)
pane=$(jq -r .result.pane.pane_id <<<"$cur")
tab=$(jq -r .result.pane.tab_id <<<"$cur")
ws=$(jq -r .result.pane.workspace_id <<<"$cur")
log "mode=${1:-none} resolved pane=$pane tab=$tab ws=$ws"

case "${1:-}" in
break)
  # Pane leaves the split and becomes its own tab.
  herdr pane move "$pane" --new-tab --focus >>"$LOG" 2>&1
  ;;
join)
  # Pick a pane living in another tab of this workspace and pull it in here.
  src=$(herdr pane list |
    jq -r --arg ws "$ws" --arg tab "$tab" '
      .result.panes[]
      | select(.workspace_id == $ws and .tab_id != $tab)
      | "\(.pane_id)\t\(.tab_id)  \(.terminal_title_stripped)"' |
    fzf --delimiter='\t' --with-nth=2 --prompt='join pane > ' --height=100% |
    cut -f1) || exit 0
  [ -n "$src" ] || exit 0
  log "join src=$src -> tab=$tab"
  herdr pane move "$src" --tab "$tab" --split right --focus >>"$LOG" 2>&1
  ;;
*)
  echo "usage: ${0##*/} break|join" >&2
  exit 2
  ;;
esac
