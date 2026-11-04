#!/bin/bash
# Claude Code Statusline: Claude HUD (jarrodwatts/claude-hud), then the lines the task-progress and token-weather mods
# leave for this session. (The Firebase/gcloud PROD warning that ran `firebase use` and `gcloud` on every refresh was
# removed on 2026-11-03: it made each refresh take over a second.)

# Read input from stdin
input=$(cat)

# This runs the launcher from the plugin cache because plugin sync installs the plugin on 집맥 but not the copy
# /claude-hud:setup makes. Any installed copy works: the launcher picks the newest version. Node, not bun: bun reads
# .env and bunfig.toml from the session folder, and a bunfig.toml preload would run that repo's code on every refresh.
hud_launchers=("${CLAUDE_CONFIG_DIR:-$HOME/.claude}"/plugins/cache/claude-hud/claude-hud/*/scripts/statusline.mjs)
printf '%s' "$input" | node "${hud_launchers[0]}" 2>/dev/null

session_id="$(echo "$input" | jq -r '.session_id // empty')"

# Right under the HUD, where its tools line was (showTools is off): the task-progress mod's two lines, elapsed of
# estimated minutes and done of total steps (~/.claude/skills/task-progress writes them; a mod cannot print into this
# command's output). Rows are "<min COLUMNS>\t<time line>\t<steps line>", widest first, keyed by the HUD's bar width
# rule; print the first whose min fits COLUMNS. An empty file (no task) prints nothing.
progress="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/statusline/task-progress/${session_id}.tsv"
if [ -n "$session_id" ] && [ -s "$progress" ]; then
	awk -F'\t' -v c="${COLUMNS:-0}" 'c == 0 || $1 <= c { print $2; print $3; exit }' "$progress"
fi

# Then, in a pane that opened panes with pane-spawn, one steps line per such pane: 요유 reads only the lead's pane
# (2026-11-01), so the steps of the panes under it show here. pane-spawn names the agent spawn-<opener's terminal>-<n>,
# which a pane move does not change. Each line is that pane's name and the steps line of its narrowest row.
# ponytail: a long name plus the 40-cell row can wrap below about 80 columns; cut the name by COLUMNS if that bites.
if [ -n "$session_id" ] && [ -n "${HERDR_PANE_ID:-}" ] && command -v herdr >/dev/null 2>&1; then
	agents=$(herdr agent list 2>/dev/null)
	term=$(jq -r --arg s "$session_id" 'first(.result.agents[]? | select(.agent_session.value == $s) | .terminal_id | ltrimstr("term_")) // empty' <<<"$agents" 2>/dev/null)
	if [ -n "$term" ]; then
		jq -r --arg p "spawn-$term-" --argjson panes "$(herdr pane list 2>/dev/null || echo '{}')" '
			([$panes.result.panes[]? | {key: .pane_id, value: (.label // "")}] | from_entries) as $label
			| .result.agents[]? | select((.name // "") | startswith($p)) | select(.agent_session.value)
			| [.agent_session.value, ($label[.pane_id] // "" | gsub("[🟢🔴🫡👑] *"; "") | .[0:16])] | @tsv' <<<"$agents" 2>/dev/null |
			while IFS=$'\t' read -r sub name; do
				file="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/statusline/task-progress/${sub}.tsv"
				[ -s "$file" ] && awk -F'\t' -v n="$name" 'END { print "↳ " n "  " $3 }' "$file"
			done
	fi
fi

# Under the HUD: the token-weather mod's forecast for this session (the mod writes it on every reading; a mod cannot
# print into this command's output). Rows are "<cells>\t<ANSI line>", widest first; print the first that fits COLUMNS,
# which Claude Code sets for the status line. No file, or no row that fits: no line.
weather="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/statusline/token-weather/${session_id}.tsv"
if [ -n "$session_id" ] && [ -f "$weather" ]; then
	awk -F'\t' -v c="${COLUMNS:-0}" 'c == 0 || $1 <= c { print $2; exit }' "$weather"
fi
