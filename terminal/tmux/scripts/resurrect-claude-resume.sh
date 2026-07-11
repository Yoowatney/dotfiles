#!/usr/bin/env bash
# tmux-resurrect post-save hook: rewrite claude pane commands in the latest
# save file to `claude --resume <session-id>` so restored panes reattach to
# their conversations. Session ids are tagged on panes as @claude_session_id
# by the Claude Code SessionStart hook (~/.claude/hooks/session-env.sh).
set -euo pipefail

RESURRECT_DIR="${RESURRECT_DIR:-$HOME/.local/share/tmux/resurrect}"
SAVE_FILE=$(readlink -f "${RESURRECT_DIR}/last" 2>/dev/null) || exit 0
[ -f "$SAVE_FILE" ] || exit 0

TAB=$'\t'
TAGS=$(tmux list-panes -a -F "#{session_name}${TAB}#{window_index}${TAB}#{pane_index}${TAB}#{@claude_session_id}")

# tags go in as a first input file — BSD awk rejects newlines in -v strings
awk -F '\t' -v OFS='\t' '
NR == FNR {
  if (NF >= 4 && $4 != "") tag[$1 SUBSEP $2 SUBSEP $3] = $4
  next
}
# pane line fields: pane, session, win_idx, win_active, :flags, pane_idx,
#                   title, :cwd, pane_active, current_cmd, :full_cmd
$1 == "pane" {
  cmd = substr($NF, 2)
  id = tag[$2 SUBSEP $3 SUBSEP $6]
  if (id != "" && cmd ~ /(^|\/)claude( |$)/) {
    gsub(/ --resume[ =][^ ]+/, "", cmd)  # drop stale session refs
    gsub(/ --continue/, "", cmd)
    sub(/ +$/, "", cmd)
    $NF = ":" cmd " --resume " id
  }
}
{ print }
' <(printf '%s\n' "$TAGS") "$SAVE_FILE" > "${SAVE_FILE}.tmp" && mv "${SAVE_FILE}.tmp" "$SAVE_FILE"
