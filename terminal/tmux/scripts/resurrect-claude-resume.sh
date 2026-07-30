#!/usr/bin/env bash
# tmux-resurrect post-save hook: rewrite claude pane commands in the latest
# save file to `claude --resume <session-id>` so restored panes reattach to
# their conversations. Session ids, transcript paths and startup cwds are tagged
# on panes as @claude_session_id / @claude_transcript / @claude_cwd by the Claude
# Code SessionStart hook (~/.claude/hooks/session-env.sh).
#
# `claude --resume <id>` only looks inside ~/.claude/projects/<mangled cwd>/, so a
# pane must be restored in the cwd its session started in — hence the cwd rewrite.
# And a tagged id whose transcript does not exist (fresh session that was never
# prompted, e.g. after a failed resume) is dropped instead of re-injected: keeping
# it poisons the pane on every future restore.
set -euo pipefail

RESURRECT_DIR="${RESURRECT_DIR:-$HOME/.local/share/tmux/resurrect}"
SAVE_FILE=$(readlink -f "${RESURRECT_DIR}/last" 2>/dev/null) || exit 0
[ -f "$SAVE_FILE" ] || exit 0

TAB=$'\t'
# CLAUDE_RESUME_PANES is a test seam — normally the pane list comes from tmux
PANES=${CLAUDE_RESUME_PANES:-$(tmux list-panes -a -F "#{session_name}${TAB}#{window_index}${TAB}#{pane_index}${TAB}#{@claude_session_id}${TAB}#{@claude_transcript}${TAB}#{@claude_cwd}")}

TAGS=$(printf '%s\n' "$PANES" | while IFS="$TAB" read -r s w p id transcript cwd; do
  [ -n "$id" ] && [ -n "$cwd" ] && [ -f "${transcript:-/nonexistent}" ] || continue
  printf '%s\t%s\t%s\t%s\t%s\n' "$s" "$w" "$p" "$id" "${cwd// /\\ }"  # resurrect escapes spaces in cwd
done)

# tags go in as a first input file — BSD awk rejects newlines in -v strings
awk -F '\t' -v OFS='\t' '
NR == FNR {
  if (NF >= 5) { tag[$1 SUBSEP $2 SUBSEP $3] = $4; dir[$1 SUBSEP $2 SUBSEP $3] = $5 }
  next
}
# pane line fields: pane, session, win_idx, win_active, :flags, pane_idx,
#                   title, :cwd, pane_active, current_cmd, :full_cmd
$1 == "pane" {
  cmd = substr($NF, 2)
  if (cmd ~ /(^|\/)claude( |$)/) {
    gsub(/ --resume[ =][^ ]+/, "", cmd)  # drop stale session refs, tagged or not
    gsub(/ --continue/, "", cmd)
    sub(/ +$/, "", cmd)
    key = $2 SUBSEP $3 SUBSEP $6
    if (key in tag) {
      cmd = cmd " --resume " tag[key]
      # pane_title is not :-quoted, so resurrect (IFS=tab read) collapses it away
      # when empty and shifts every later field left — locate cwd by shape
      cwdf = ($7 ~ /^:\//) ? 7 : (($8 ~ /^:\//) ? 8 : 0)
      if (cwdf) $cwdf = ":" dir[key]     # --resume only finds ids under this cwd
    }
    $NF = ":" cmd
  }
}
{ print }
' <(printf '%s\n' "$TAGS") "$SAVE_FILE" > "${SAVE_FILE}.tmp" && mv "${SAVE_FILE}.tmp" "$SAVE_FILE"
