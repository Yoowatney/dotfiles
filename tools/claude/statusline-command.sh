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

# Last, the board items waiting on 요유 (owner:yoyoo): how many, how many past due, the longest wait, the top item
# (soonest due first), from the summary the agent-board watch job writes every 30 minutes (watch/yoyoo-turn.sh in the
# munice workspace's .agents/skills/agent-board). The status line redraws too often to ask GitHub itself, so the line
# ends with the summary's age, yellow once it is over 90 minutes old. No file, or a broken one: no line.
# (요유 2026-11-04: /monitor goes unopened, the status line is always in view.)
# ponytail: every code point from U+1100 up counts as 2 cells, which overcounts … and the like; the clip only gets shorter.
turn="$HOME/.config/munice/yoyoo-turn.json"
if [ -s "$turn" ]; then
	jq -r --argjson now "$(date +%s)" --argjson c "${COLUMNS:-0}" '
		def w: [explode[] | if . >= 4352 then 2 else 1 end] | add // 0;
		def clip($n): if w <= $n then . else
			(reduce explode[] as $ch ({s: [], n: 0, done: false};
				if .done or .n + ([$ch] | implode | w) > $n - 1 then .done = true
				else {s: (.s + [$ch]), n: (.n + ([$ch] | implode | w)), done: false} end) | (.s | implode) + "…") end;
		($now - .at) as $old
		| ($old | if . < 3600 then "\(. / 60 | floor)분 전" elif . < 172800 then "\(. / 3600 | floor)시간 전" else "\(. / 86400 | floor)일 전" end) as $age
		| "요유 차례 \(.count)건" as $head
		| (if .count > 0 and .overdue > 0 then "기한 지남 \(.overdue)건" else null end) as $over
		| (if .count > 0 then "최장 \(.oldestDays)일" else null end) as $wait
		| ([$head, $over, $wait] | map(select(. != null)) | join(" · ")) as $lead
		| (if $c > 0 then $c - ($lead | w) - (" · " | w) - (" · \($age)" | w) else 10000 end) as $room
		| (if .top != null and $room >= 6 then " · " + (.top | clip($room)) else "" end) as $top
		| "\u001b[1m\($head)\u001b[0m" + (if $over then " · \u001b[31m\($over)\u001b[0m" else "" end)
		  + (if $wait then " · \($wait)" else "" end) + $top
		  + " · " + (if $old > 5400 then "\u001b[33m" else "\u001b[2m" end) + $age + "\u001b[0m"' "$turn" 2>/dev/null
fi
