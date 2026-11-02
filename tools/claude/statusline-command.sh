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

# Under the HUD: the token-weather mod's forecast for this session (the mod writes it on every reading; a mod cannot
# print into this command's output). Rows are "<cells>\t<ANSI line>", widest first; print the first that fits COLUMNS,
# which Claude Code sets for the status line. No file, or no row that fits: no line.
weather="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/statusline/token-weather/${session_id}.tsv"
if [ -n "$session_id" ] && [ -f "$weather" ]; then
	awk -F'\t' -v c="${COLUMNS:-0}" 'c == 0 || $1 <= c { print $2; exit }' "$weather"
fi
