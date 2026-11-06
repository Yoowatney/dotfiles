#!/bin/bash
# Claude Code Statusline: Claude HUD (jarrodwatts/claude-hud) with Codex's week on its usage line, then the lines the task-progress and token-weather mods
# leave for this session. (The Firebase/gcloud PROD warning that ran `firebase use` and `gcloud` on every refresh was
# removed on 2026-11-03: it made each refresh take over a second.)

# Read input from stdin
input=$(cat)

# This runs the launcher from the plugin cache because plugin sync installs the plugin on 집맥 but not the copy
# /claude-hud:setup makes. Any installed copy works: the launcher picks the newest version. Node, not bun: bun reads
# .env and bunfig.toml from the session folder, and a bunfig.toml preload would run that repo's code on every refresh.
hud_launchers=("${CLAUDE_CONFIG_DIR:-$HOME/.claude}"/plugins/cache/claude-hud/claude-hud/*/scripts/statusline.mjs)
hud=$(printf '%s' "$input" | node "${hud_launchers[0]}" 2>/dev/null)

# Codex's week beside Claude's, read by codex-week as pane-spawn reads it. Match claude-hud 0.14.1's
# render/frame (10/6/4 cells), render/colors (rounded fill, quota colors, dim empty cells), and render/time (ceil minutes for resets,
# floor minutes for record age). Keep this independent of the plugin so a missing HUD still shows Codex.
codex_text() {
	local used resets at now
	IFS=$'\t' read -r used resets at < <("$(dirname "$(readlink -f "$0")")/../agent-hooks/codex-week" 2>/dev/null)
	[[ "$used" =~ ^[0-9]+([.][0-9]+)?$ && "$resets" =~ ^[0-9]+$ ]] || { echo "Codex (no usage data)"; return; }
	now=$(date +%s)
	awk -v used="$used" -v resets="$resets" -v at="$at" -v now="$now" -v cols="${COLUMNS:-0}" '
		function duration(mins, hours, days) {
			if (mins < 60) return mins "m"
			hours = int(mins / 60)
			if (hours < 24) return hours "h" (mins % 60 ? " " mins % 60 "m" : "")
			days = int(hours / 24)
			return days "d" (hours % 24 ? " " hours % 24 "h" : "")
		}
		BEGIN {
			width = !cols || cols >= 100 ? 10 : cols >= 60 ? 6 : 4
			if (resets <= now) used = 0
			used = used > 100 ? 100 : used
			filled = int(used / 100 * width + 0.5)
			color = used >= 90 ? "\033[31m" : used >= 75 ? "\033[95m" : "\033[94m"
			printf "Codex %s", color
			for (i = 0; i < filled; i++) printf "█"
			printf "\033[2m"
			for (i = filled; i < width; i++) printf "░"
			printf "\033[0m"
			note = resets <= now ? "reset" : "resets in " duration(int((resets - now + 59) / 60))
			if (at ~ /^[0-9]+$/ && now - at >= 3600)
				note = note "; updated " duration(int((now - at) / 60)) " ago"
			printf " %s%d%%\033[0m (%s)\n", color, int(used + 0.5), note
		}'
}
# The HUD's lines, Codex at the end of its usage line when that fits COLUMNS (a wrapped status line reads as broken), else on
# the line under the HUD. Cells: ANSI codes take none, Hangul and other wide characters two.
printf '%s' "$hud" | perl -CSDA -e '
	my ($codex, $cols) = @ARGV; my $done;
	sub plain { (my $t = shift) =~ s/\e\[[0-9;]*m//g; $t }
	sub cells { my $n = 0;
		$n += /[\x{1100}-\x{115F}\x{2E80}-\x{A4CF}\x{AC00}-\x{D7A3}\x{F900}-\x{FAFF}\x{FE30}-\x{FE4F}\x{FF00}-\x{FF60}\x{FFE0}-\x{FFE6}]/ ? 2 : 1 for split //, plain(shift); $n }
	my $tail = "\e[38;2;153;153;153m \x{2502} \e[0m$codex"; # " │ " in the grey of the HUD'"'"'s own separators
	while (<STDIN>) { chomp;
		if (!$done && plain($_) =~ /Usage|Weekly/ && (!$cols || cells($_) + cells($tail) <= $cols)) { $_ .= $tail; $done = 1 }
		print "$_\n" }
	print "$codex\n" unless $done' "$(codex_text)" "${COLUMNS:-0}"

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
# which a pane move does not change. Each line is that pane's name and the steps line of its narrowest row. A lead's panes
# run on the hidden workers server, where this line is the one view of them; a pane on workers reads no other server.
# ponytail: a long name plus the 40-cell row can wrap below about 80 columns; cut the name by COLUMNS if that bites.
spawned_steps() { # <agent list json> <pane list json>
	jq -r --arg p "spawn-$term-" --argjson panes "${2:-null}" '
		([$panes.result.panes[]? | {key: .pane_id, value: (.label // "")}] | from_entries) as $label
		| .result.agents[]? | select((.name // "") | startswith($p)) | select(.agent_session.value)
		| [.agent_session.value, ($label[.pane_id] // "" | gsub("[🟢🔴🫡👑] *"; "") | .[0:16])] | @tsv' <<<"$1" 2>/dev/null |
		while IFS=$'\t' read -r sub name; do
			file="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/statusline/task-progress/${sub}.tsv"
			[ -s "$file" ] && awk -F'\t' -v n="$name" 'END { print "↳ " n "  " $3 }' "$file"
		done
}
if [ -n "$session_id" ] && [ -n "${HERDR_PANE_ID:-}" ] && command -v herdr >/dev/null 2>&1; then
	agents=$(herdr agent list 2>/dev/null)
	term=$(jq -r --arg s "$session_id" 'first(.result.agents[]? | select(.agent_session.value == $s) | .terminal_id | ltrimstr("term_")) // empty' <<<"$agents" 2>/dev/null)
	if [ -n "$term" ]; then
		spawned_steps "$agents" "$(herdr pane list 2>/dev/null)"
		case "${HERDR_SOCKET_PATH:-}" in
			*/sessions/workers/*) ;;
			*)
				# herdr's CLI waits on a stuck server for ever, and this line has to come back, so a stuck workers costs a few seconds and only its own rows.
				on_workers() { perl -e 'alarm shift; exec @ARGV' "${STATUSLINE_WORKERS_LIMIT:-2}" herdr --session workers "$@" 2>/dev/null; }
				hidden=$(on_workers agent list) && spawned_steps "$hidden" "$(on_workers pane list)"
				;;
		esac
	fi
fi

# Under the HUD: the token-weather mod's forecast for this session (the mod writes it on every reading; a mod cannot
# print into this command's output). Rows are "<cells>\t<ANSI line>", widest first; print the first that fits COLUMNS,
# which Claude Code sets for the status line. No file, or no row that fits: no line.
weather="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/statusline/token-weather/${session_id}.tsv"
if [ -n "$session_id" ] && [ -f "$weather" ]; then
	awk -F'\t' -v c="${COLUMNS:-0}" 'c == 0 || $1 <= c { print $2; exit }' "$weather"
fi
