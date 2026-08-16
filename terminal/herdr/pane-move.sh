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

# --no-preview because the shell exports a global `--preview 'head -100 {}'`,
# which treats a row of this list as a filename and fills half the popup with
# "No such file or directory". The rows carry their own context instead.
pick() {
  fzf --delimiter='\t' --with-nth=2 --no-preview --height=100% \
    --header="$1" --prompt='> '
}

# A tab named "2" sitting at position 4 renders as "4. 2", which says nothing, so
# rows name what is actually inside: pane labels, or the working directory for a
# bare shell whose title is the useless user@host:path form.
INSIDE='
  def nice:
    (.label // (.terminal_title_stripped | sub("^[^:]+@[^:]+:"; "")))
    | if length > 24 then .[0:23] + "…" else . end;
'

cur=$(herdr pane current)
pane=$(jq -r .result.pane.pane_id <<<"$cur")
tab=$(jq -r .result.pane.tab_id <<<"$cur")
ws=$(jq -r .result.pane.workspace_id <<<"$cur")
log "mode=${1:-none} resolved pane=$pane tab=$tab ws=$ws"

case "${1:-}" in
out)
  # This pane leaves for another tab — a fresh one, or one that already exists.
  dst=$({
    printf '__new__\t+  new tab\n'
    jq -rn --argjson t "$(herdr tab list)" --argjson p "$(herdr pane list)" \
      --arg ws "$ws" --arg tab "$tab" "$INSIDE"'
      ($p.result.panes | map(select(.workspace_id == $ws))) as $panes
      | $t.result.tabs[]
      | select(.workspace_id == $ws and .tab_id != $tab)
      | . as $tb
      | ($panes | map(select(.tab_id == $tb.tab_id) | nice) | join(", ")) as $has
      | "\($tb.tab_id)\ttab \($tb.number)  \"\($tb.label)\"   holds: \($has)"'
  } | pick 'move this pane to which tab?' | cut -f1) || exit 0
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
  src=$(jq -rn --argjson t "$(herdr tab list)" --argjson p "$(herdr pane list)" \
    --arg ws "$ws" --arg tab "$tab" "$INSIDE"'
    ($t.result.tabs | map({key: .tab_id, value: .}) | from_entries) as $tabs
    | $p.result.panes[]
    | select(.workspace_id == $ws and .tab_id != $tab)
    | "\(.pane_id)\t\(nice)   in tab \($tabs[.tab_id].number)  \"\($tabs[.tab_id].label)\""' |
    pick 'bring which pane into this tab?' | cut -f1) || exit 0
  [ -n "$src" ] || exit 0
  log "in src=$src -> tab=$tab"
  herdr pane move "$src" --tab "$tab" --split right --focus >>"$LOG" 2>&1
  ;;
*)
  echo "usage: ${0##*/} out|in" >&2
  exit 2
  ;;
esac
