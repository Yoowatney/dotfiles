#!/bin/bash
# Claude Code Statusline: Claude HUD (jarrodwatts/claude-hud) with Codex's week on its usage line, then the lines the task-progress and token-weather mods
# leave for this session. (The Firebase/gcloud PROD warning that ran `firebase use` and `gcloud` on every refresh was
# removed on 2026-11-03: it made each refresh take over a second.)

# Read input from stdin
input=$(cat)

# agy sends official statusLine metadata here; Claude only reads its local cache.
# Store no account, workspace, transcript or prompt fields. Never start a model.
agy_usage() {
python3 -I -c '
import datetime, json, math, os, pathlib, re, sys, tempfile, time, unicodedata
mode = sys.argv[1]
path = pathlib.Path(os.environ.get("XDG_STATE_HOME", str(pathlib.Path.home() / ".local/state"))) / "agent-quota/agy.json"
now = int(time.time())
keys = ("gemini-5h", "gemini-weekly", "3p-5h", "3p-weekly")
def epoch(value):
    if not isinstance(value, str): return None
    try: return datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, OverflowError): return None
def valid(bucket):
    if not isinstance(bucket, dict): return False
    fraction = bucket.get("remaining_fraction")
    return type(fraction) in (int, float) and math.isfinite(fraction) and 0 <= fraction <= 1 and epoch(bucket.get("reset_time")) is not None
try:
    if mode == "capture":
        source = json.load(sys.stdin)
        if not isinstance(source, dict): sys.exit(0)
        quotas = source.get("quota") or {}
        if not isinstance(quotas, dict): quotas = {}
        plan = source.get("plan_tier", "")
        if not isinstance(plan, str): plan = ""
        # Remove terminal controls and bound metadata before it reaches a terminal.
        plan = "".join(c for c in plan if c.isprintable())[:60]
        data = {"captured_at": now, "plan_tier": plan, "quota": {
            k: {"remaining_fraction": quotas[k]["remaining_fraction"], "reset_time": quotas[k]["reset_time"]}
            for k in keys if valid(quotas.get(k))}}
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".agy-", dir=path.parent)
        try:
            with os.fdopen(fd, "w") as stream: json.dump(data, stream)
            os.replace(name, path)
        finally:
            if os.path.exists(name): os.unlink(name)
        sys.exit(0)
    data = json.loads(path.read_text())
    quotas = data.get("quota") or {}
    if not any(valid(quotas.get(k)) for k in keys): raise ValueError("no quota")
    cols = int(os.environ.get("COLUMNS", "0"))
    width = 10 if not cols or cols >= 100 else 6 if cols >= 60 else 4
    def duration(seconds):
        minutes = max(0, math.ceil(seconds / 60))
        if minutes < 60: return str(minutes) + "m"
        hours, minutes = divmod(minutes, 60)
        if hours < 24: return str(hours) + "h" + (" " + str(minutes) + "m" if minutes else "")
        days, hours = divmod(hours, 24)
        return str(days) + "d" + (" " + str(hours) + "h" if hours else "")
    def bucket(key):
        value = quotas.get(key)
        if not valid(value): return "(no usage data)"
        reset = epoch(value["reset_time"])
        used = (1 - value["remaining_fraction"]) * 100
        if reset <= now and not (reset == 0 and used == 0): return "(reset; awaiting update)"
        color = "\033[31m" if used >= 90 else "\033[95m" if used >= 75 else "\033[94m"
        fill = int(used * width / 100 + .5)
        bar = color + "█" * fill + "\033[2m" + "░" * (width - fill) + "\033[0m"
        note = "not started" if reset == 0 else "resets in " + duration(reset - now)
        return bar + " " + color + str(int(used + .5)) + "%\033[0m (" + note + ")"
    def cells(text):
        return sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in ("W", "F") else 1
                   for c in re.sub(r"\x1b\[[0-9;]*m", "", text))
    plan = data.get("plan_tier") or "plan unknown"
    plan = "".join(c for c in str(plan) if c.isprintable())[:60]
    age = now - data.get("captured_at", 0)
    title = "agy (" + plan + ")" + (" (updated " + duration(age // 60 * 60) + " ago)" if age >= 3600 else "")
    for group, label in (("gemini", "Gemini"), ("3p", "3P")):
        if not any(valid(quotas.get(group + suffix)) for suffix in ("-5h", "-weekly")): continue
        prefix = title if group == "gemini" else "agy"
        first = label + " 5h " + bucket(group + "-5h")
        second = "Weekly " + bucket(group + "-weekly")
        line = prefix + " " + first + " │ " + second
        if not cols or cells(line) <= cols: print(line)
        else:
            if group == "gemini": print(title)
            print("agy " + first)
            print("agy " + label + " " + second)
except (OSError, ValueError, TypeError, AttributeError, KeyError, OverflowError):
    if mode != "capture": print("agy (no usage data)")
' "$1"
}
case "${1:-}" in
    --capture-agy) printf '%s' "$input" | agy_usage capture; exit 0 ;;
    --agy-text) agy_usage render; exit 0 ;;
esac

# Cache only official quota fields, tied to the current account and assistant response time.
# Missing metadata or an unwritable cache must never break the status line.
printf '%s' "$input" | python3 -I "$(dirname "$(readlink -f "$0")")/../agent-hooks/agent-quota" capture-claude 2>/dev/null || true

# This runs the launcher from the plugin cache because plugin sync installs the plugin on 집맥 but not the copy
# /claude-hud:setup makes. Any installed copy works: the launcher picks the newest version. Node, not bun: bun reads
# .env and bunfig.toml from the session folder, and a bunfig.toml preload would run that repo's code on every refresh.
hud_launchers=("${CLAUDE_CONFIG_DIR:-$HOME/.claude}"/plugins/cache/claude-hud/claude-hud/*/scripts/statusline.mjs)
hud=$(printf '%s' "$input" | node "${hud_launchers[0]}" 2>/dev/null)

# Codex's week beside Claude's, read by codex-week for display only. Match claude-hud 0.14.1's
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

agy_usage render

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
