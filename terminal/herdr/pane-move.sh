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

# TabInfo.number is not the position on the tab bar — it is an id ordinal, so
# closing a tab leaves a hole and the second tab of a workspace can report 4.
# Rows count position in the workspace's own tab order instead, which is what is
# actually on screen. Contents come along too: a tab named "2" identifies nothing
# by itself, so rows name its panes, falling back to the working directory when a
# bare shell only offers the useless user@host:path title.
ROWS='
  def nice:
    (.label // (.terminal_title_stripped | sub("^[^:]+@[^:]+:"; "")))
    | if length > 24 then .[0:23] + "…" else . end;
  def here($ws): map(select(.workspace_id == $ws));
  def at($tabs): $tabs | to_entries | map({key: .value.tab_id, value: (.key + 1)})
    | from_entries;
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
      --arg ws "$ws" --arg tab "$tab" "$ROWS"'
      ($t.result.tabs | here($ws)) as $tabs
      | ($p.result.panes | here($ws)) as $panes
      | at($tabs) as $pos
      | $tabs[]
      | select(.tab_id != $tab)
      | . as $tb
      | ($panes | map(select(.tab_id == $tb.tab_id) | nice) | join(", ")) as $has
      | "\($tb.tab_id)\ttab \($pos[$tb.tab_id])  \"\($tb.label)\"   holds: \($has)"'
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
    --arg ws "$ws" --arg tab "$tab" "$ROWS"'
    ($t.result.tabs | here($ws)) as $tabs
    | at($tabs) as $pos
    | ($tabs | map({key: .tab_id, value: .label}) | from_entries) as $name
    | $p.result.panes | here($ws)[]
    | select(.tab_id != $tab)
    | "\(.pane_id)\t\(nice)   in tab \($pos[.tab_id])  \"\($name[.tab_id])\""' |
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
