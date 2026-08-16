#!/usr/bin/env bash
# Moves a pane between tabs, which herdr can already do (`herdr pane move`) but
# binds to no key. Everything below is argument plumbing for that one command.
#
# Two directions, not three: tmux splits this into break-pane and join-pane, but
# "break" is only a send whose destination does not exist yet, so it lives in the
# out picker as the first row instead of owning a key of its own.
#
# ponytail: no state of its own; the server is the source of truth for what is
# focused. Every run appends to the log, so a failure inside a popup that closes
# on exit is still readable afterwards.
set -euo pipefail

LOG=/tmp/herdr-pane-move.log
log() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*" >>"$LOG"; }

pick() { fzf --delimiter='\t' --with-nth=2 --height=100% --prompt="$1"; }

cur=$(herdr pane current)
pane=$(jq -r .result.pane.pane_id <<<"$cur")
tab=$(jq -r .result.pane.tab_id <<<"$cur")
ws=$(jq -r .result.pane.workspace_id <<<"$cur")
log "mode=${1:-none} resolved pane=$pane tab=$tab ws=$ws"

case "${1:-}" in
out)
  # This pane leaves for another tab — a fresh one, or one that already exists.
  dst=$({
    echo -e "__new__\t+ new tab"
    herdr tab list | jq -r --arg ws "$ws" --arg tab "$tab" '
      .result.tabs[]
      | select(.workspace_id == $ws and .tab_id != $tab)
      | "\(.tab_id)\t\(.number). \(.label)"'
  } | pick 'move this pane to > ' | cut -f1) || exit 0
  [ -n "$dst" ] || exit 0
  log "out pane=$pane -> $dst"
  if [ "$dst" = "__new__" ]; then
    herdr pane move "$pane" --new-tab --focus >>"$LOG" 2>&1
  else
    herdr pane move "$pane" --tab "$dst" --split right --focus >>"$LOG" 2>&1
  fi
  ;;
in)
  # A pane living in another tab of this workspace comes here as a split.
  src=$(herdr pane list |
    jq -r --arg ws "$ws" --arg tab "$tab" '
      .result.panes[]
      | select(.workspace_id == $ws and .tab_id != $tab)
      | "\(.pane_id)\t\(.tab_id)  \(.terminal_title_stripped)"' |
    pick 'bring which pane here > ' | cut -f1) || exit 0
  [ -n "$src" ] || exit 0
  log "in src=$src -> tab=$tab"
  herdr pane move "$src" --tab "$tab" --split right --focus >>"$LOG" 2>&1
  ;;
*)
  echo "usage: ${0##*/} out|in" >&2
  exit 2
  ;;
esac
