#!/bin/bash
# Checks statusline-command.sh: the HUD first, no PROD warning on any project, nothing left of the old status line.
# Run: bash tools/claude/statusline-command.test.sh
set -u

SCRIPT="$(cd "$(dirname "$0")" && pwd)/statusline-command.sh"
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
INPUT='{"model":{"display_name":"Opus"},"workspace":{"current_dir":"'"$T"'"},"context_window":{"used_percentage":12,"context_window_size":200000}}'
fails=0
export CLOUDSDK_CORE_PROJECT=test-dev   # a dev project, whatever this machine's gcloud points at
export HOME="$T/home"                   # no real Codex records: the Codex part reads ~/.codex/sessions
export CODEX_HOME="$HOME/.codex"         # ignore an inherited Codex profile too
# Freeze the clock at the process boundary, including codex-week, so minute/reset boundaries are exact.
mkdir -p "$T/bin"
export STATUSLINE_TEST_NOW=1804488000
printf '#!/bin/bash\nif [ "$*" = +%%s ]; then echo "$STATUSLINE_TEST_NOW"; else exec /bin/date "$@"; fi\n' >"$T/bin/date"
chmod +x "$T/bin/date"
export PATH="$T/bin:$PATH"

run() { printf '%s' "$INPUT" | bash "$SCRIPT" 2>&1; }
fake_hud() {   # <config dir> <launcher source>
	mkdir -p "$1/plugins/cache/claude-hud/claude-hud/9.9.9/scripts"
	printf '%s\n' "$2" >"$1/plugins/cache/claude-hud/claude-hud/9.9.9/scripts/statusline.mjs"
}
check() {   # <name> <output> <must match> [<must not match>]
	if ! grep -qE "$3" <<<"$2" || { [ $# -gt 3 ] && grep -qE "$4" <<<"$2"; }; then
		echo "FAIL $1"
		printf '%s\n' "$2" | cat -v | sed 's/^/  /'
		fails=$((fails + 1))
	else
		echo "ok   $1"
	fi
}
check_exact() { # <name> <output> <expected>, including ANSI styling
	if [ "$2" = "$3" ]; then
		echo "ok   $1"
	else
		echo "FAIL $1"
		printf '  expected: %s\n  actual:   %s\n' "$3" "$2" | cat -v
		fails=$((fails + 1))
	fi
}

fake_hud "$T/hud" 'console.log("[Opus] Context 12%")'   # a machine without the real HUD plugin must pass too
out=$(CLAUDE_CONFIG_DIR="$T/hud" run)
check "installed HUD prints its lines" "$out" 'Context'
check "HUD is the first line on a dev project" "$(head -1 <<<"$out")" '\[Opus\]'
check "old env line is gone" "$out" 'Context' '👤|로그인필요|미설정'

# The PROD warning was removed (it ran firebase and gcloud on every refresh, over a second each time).
mkdir "$T/fb"
printf '#!/bin/bash\necho test-f06c6\n' >"$T/bin/firebase"
printf '#!/bin/bash\necho test-f06c6\n' >"$T/bin/gcloud"
chmod +x "$T/bin/firebase" "$T/bin/gcloud"
echo '{}' >"$T/fb/.firebaserc"
out=$(INPUT=${INPUT//$T/$T/fb} PATH="$T/bin:$PATH" CLOUDSDK_CORE_PROJECT=test-f06c6 CLAUDE_CONFIG_DIR="$T/hud" run)
check "no PROD warning on a PROD project" "$out" 'Context' 'PROD'
check "HUD is still the first line on a PROD project" "$(head -1 <<<"$out")" '\[Opus\]'

out=$(CLAUDE_CONFIG_DIR="$T/empty" run)
check "missing HUD prints no old lines, only the Codex line" "$out" '^Codex ' 'Opus|👤'

fake_hud "$T/broken" 'process.exit(1)'
out=$(CLAUDE_CONFIG_DIR="$T/broken" run)
check "HUD that prints nothing leaves no old lines, only the Codex line" "$out" '^Codex ' 'Opus|👤'

# bun loads .env from the session folder unless told not to, and these repos keep secrets there
fake_hud "$T/leak" 'console.log("leak=" + process.env.STATUSLINE_TEST_SECRET)'
mkdir "$T/repo"
echo 'STATUSLINE_TEST_SECRET=loaded' >"$T/repo/.env"
out=$(cd "$T/repo" && CLAUDE_CONFIG_DIR="$T/leak" run)
check "HUD does not load .env from the session folder" "$out" 'leak=undefined'

# a repo must not get code run on every refresh just by being the session folder
echo 'preload = ["./preload.js"]' >"$T/repo/bunfig.toml"
echo 'console.log("preloaded")' >"$T/repo/preload.js"
out=$(cd "$T/repo" && CLAUDE_CONFIG_DIR="$T/leak" run)
check "HUD ignores bunfig.toml in the session folder" "$out" 'leak=undefined' 'preloaded'

# Codex's week beside Claude's (2026-11-06), from the newest Codex session record, as pane-spawn reads it.
codex_record() { # <used percent> <reset epoch> <seconds since written>: one record as Codex writes it
	mkdir -p "$HOME/.codex/sessions/2026/11/06"
	jq -nc --argjson u "$1" --argjson r "$2" --arg at "$(date -u -r $(($(date +%s) - $3)) +%Y-%m-%dT%H:%M:%S.000Z)" \
		'{timestamp: $at, type: "event_msg", payload: {type: "token_count", rate_limits: {limit_id: "codex", primary: {used_percent: $u, window_minutes: 10080, resets_at: $r}, secondary: null}}}' \
		>"$HOME/.codex/sessions/2026/11/06/rollout-a.jsonl"
}
plain() { sed $'s/\e\\[[0-9;]*m//g'; }
fake_hud "$T/usage" 'console.log("[Opus]\nContext 12% │ Usage Weekly \x1b[94m██████\x1b[2m░░░░\x1b[0m \x1b[94m63%\x1b[0m (resets in 1d 10h)")'
reset=$(($(date +%s) + 5 * 86400 + 6 * 3600))
# Literal expectations from claude-hud 0.14.1 render/colors.ts: quotaBar + formatQuotaPercent.
# Catch absent/wrong band colors, DIM on the wrong cells, and color leaking into the reset notice.
for row in '0 94 - ░░░░░░░░░░ 0' '48 94 █████ ░░░░░ 48' '74.9 94 ███████ ░░░ 75' '75 95 ████████ ░░ 75' '89.9 95 █████████ ░ 90' '90 31 █████████ ░ 90' '100 31 ██████████ - 100' '123 31 ██████████ - 100'; do
	read -r used color filled empty percent <<<"$row"
	[ "$filled" != - ] || filled=''
	[ "$empty" != - ] || empty=''
	codex_record "$used" "$reset" 60
	out=$(COLUMNS=200 CLAUDE_CONFIG_DIR="$T/empty" run)
	check_exact "$used%: HUD quota color, dim empty cells, colored percent and resets" "$out" \
		"Codex $(printf '\033[%sm%s\033[2m%s\033[0m \033[%sm%s%%\033[0m' "$color" "$filled" "$empty" "$color" "$percent") (resets in 5d 6h)"
done
codex_record 90 "$(date +%s)" 60
out=$(COLUMNS=200 CLAUDE_CONFIG_DIR="$T/empty" run)
check_exact "reset clears critical coloring as well as usage" "$out" \
	$'Codex \e[94m\e[2m░░░░░░░░░░\e[0m \e[94m0%\e[0m (reset)'
codex_record 46.0 "$reset" 60
out=$(COLUMNS=200 CLAUDE_CONFIG_DIR="$T/usage" run | plain)
check "Codex uses the HUD's ten-cell rounded bar and reset wording" "$(sed -n 2p <<<"$out")" 'Usage Weekly ██████░░░░ 63% \(resets in 1d 10h\) │ Codex █████░░░░░ 46% \(resets in 5d 6h\)$'
check "and adds no line of its own" "$(wc -l <<<"$out" | tr -d ' ')" '^2$'
# 60 cells of HUD + 41 cells of separator/Codex. ANSI styling takes no cells.
out=$(COLUMNS=101 CLAUDE_CONFIG_DIR="$T/usage" run | plain)
check "101 columns: exact fit stays on the usage line" "$(sed -n 2p <<<"$out")" '│ Codex █████░░░░░ 46%'
out=$(COLUMNS=100 CLAUDE_CONFIG_DIR="$T/usage" run | plain)
check "100 columns: the usage line stays as the HUD drew it" "$(sed -n 2p <<<"$out")" '\(resets in 1d 10h\)$'
check "and Codex gets the line under the HUD" "$(sed -n 3p <<<"$out")" '^Codex █████░░░░░ 46% \(resets in 5d 6h\)$'
for row in '100 █████░░░░░' '99 ███░░░' '60 ███░░░' '59 ██░░' '1 ██░░' '0 █████░░░░░'; do
	read -r cols bar <<<"$row"
	out=$(COLUMNS="$cols" CLAUDE_CONFIG_DIR="$T/usage" run | plain)
	check "$cols columns: bar width matches HUD" "$out" "Codex $bar 46%"
done
out=$(env -u COLUMNS CLAUDE_CONFIG_DIR="$T/usage" bash -c 'printf "%s" "$1" | bash "$2"' _ "$INPUT" "$SCRIPT" | plain)
check "unknown columns: ten cells like HUD" "$out" 'Codex █████░░░░░ 46%'
codex_record 45 "$reset" 60
out=$(COLUMNS=200 CLAUDE_CONFIG_DIR="$T/usage" run | plain)
check "half a bar cell rounds up like Math.round" "$out" 'Codex █████░░░░░ 45%'
codex_record 46.5 "$reset" 60
out=$(COLUMNS=200 CLAUDE_CONFIG_DIR="$T/usage" run | plain)
check "fractional percent rounds up instead of printf ties-to-even" "$out" 'Codex █████░░░░░ 47%'
for row in '454500 5d 6h' '172800 2d' '86400 1d' '86399 1d' '86340 23h 59m' '5401 1h 31m' '5400 1h 30m' '3600 1h' '60 1m' '1 1m'; do
	read -r seconds duration <<<"$row"
	codex_record 46 "$(($(date +%s) + seconds))" 60
	out=$(COLUMNS=200 CLAUDE_CONFIG_DIR="$T/usage" run | plain)
	check "$seconds seconds: HUD rounds minutes up and omits zero units" "$out" "Codex █████░░░░░ 46% \\(resets in $duration\\)$"
done
past=$(($(date +%s) - 60))
codex_record 90 "$past" 60
out=$(COLUMNS=200 CLAUDE_CONFIG_DIR="$T/usage" run | plain)
check "a reset week: empty bar and English parenthetical notice" "$out" '│ Codex ░░░░░░░░░░ 0% \(reset\)$'
codex_record 90 "$(date +%s)" 60
out=$(COLUMNS=200 CLAUDE_CONFIG_DIR="$T/usage" run | plain)
check "reset at this exact second also clears the bar" "$out" 'Codex ░░░░░░░░░░ 0% \(reset\)$'
codex_record 46 "$reset" $((3 * 3600 + 60))
out=$(COLUMNS=200 CLAUDE_CONFIG_DIR="$T/usage" run | plain)
check "an old record keeps its age in the same parenthetical style" "$out" 'Codex █████░░░░░ 46% \(resets in 5d 6h; updated 3h 1m ago\)$'
codex_record 46 "$reset" $((2 * 86400 + 60))
out=$(COLUMNS=200 CLAUDE_CONFIG_DIR="$T/usage" run | plain)
check "a record over a day old uses HUD day units" "$out" 'Codex █████░░░░░ 46% \(resets in 5d 6h; updated 2d ago\)$'
codex_record 46 "$reset" 3599
out=$(COLUMNS=200 CLAUDE_CONFIG_DIR="$T/usage" run | plain)
check "a record under an hour old needs no age" "$out" 'Codex █████░░░░░ 46% \(resets in 5d 6h\)$'
codex_record 46 "$reset" 3600
out=$(COLUMNS=200 CLAUDE_CONFIG_DIR="$T/usage" run | plain)
check "an hour-old record starts showing its age" "$out" '\(resets in 5d 6h; updated 1h ago\)$'
codex_record 90 "$past" 7200
out=$(COLUMNS=200 CLAUDE_CONFIG_DIR="$T/usage" run | plain)
check "a stale reset week keeps both notices" "$out" 'Codex ░░░░░░░░░░ 0% \(reset; updated 2h ago\)$'
echo '{"type":"event_msg","payload":{"rate_limits": broken' >"$HOME/.codex/sessions/2026/11/06/rollout-a.jsonl"
out=$(COLUMNS=200 CLAUDE_CONFIG_DIR="$T/usage" run | plain)
check "a broken record: English parenthetical notice and HUD still shows" "$out" '│ Codex \(no usage data\)$'
check "the HUD's first line is untouched" "$(head -1 <<<"$out")" '^\[Opus\]$'
rm -rf "$HOME/.codex"
out=$(COLUMNS=200 CLAUDE_CONFIG_DIR="$T/usage" run | plain)
check "no Codex records at all: says so" "$out" '│ Codex \(no usage data\)$'
codex_record 46 "$reset" 60
out=$(COLUMNS=200 CLAUDE_CONFIG_DIR="$T/hud" run | plain)
check "a HUD with no usage line: Codex gets the line under it" "$(sed -n 2p <<<"$out")" '^Codex █████░░░░░ 46% \(resets in 5d 6h\)$'
# A 6-cell bar at this width; Hangul takes two cells and ANSI takes none: 23 + 37 = 60.
fake_hud "$T/hangul" 'console.log("\x1b[2m한글 Usage 한글한글한글\x1b[0m")'
out=$(COLUMNS=60 CLAUDE_CONFIG_DIR="$T/hangul" run | plain)
check "wide Hangul and ANSI fit when there is enough room" "$(head -1 <<<"$out")" '│ Codex ███░░░ 46%'
fake_hud "$T/hangul" 'console.log("\x1b[2m한글 Usage 한글한글한글한글\x1b[0m")'
out=$(COLUMNS=60 CLAUDE_CONFIG_DIR="$T/hangul" run | plain)
check "wide Hangul forces a separate line when cell width exceeds columns" "$(sed -n 2p <<<"$out")" '^Codex ███░░░ 46%'
rm -rf "$HOME/.codex"

# 요유 reads only the lead's pane, and a lead's panes run on the hidden workers server: their steps show here all the same.
mkdir "$T/fake"
cat >"$T/fake/herdr" <<EOF
#!/bin/bash
case "\${HERDR_SOCKET_PATH:-}" in */sessions/workers/*) s=workers ;; *) s=default ;; esac
[ "\$1" = --session ] && { s=\$2; shift 2; echo "\$s" >>"$T/fake/calls"; }
[ "\$s" = workers ] && [ -e "$T/fake/stuck" ] && exec /bin/sleep 30 # herdr's CLI waits on a stuck server for ever
cat "$T/fake/\$s-\$1s.json" 2>/dev/null || { echo '{"error":{"code":"server_not_running"}}'; exit 1; }
EOF
chmod +x "$T/fake/herdr"
agent_row() { printf '{"pane_id":"%s","terminal_id":"term_%s","name":"%s","agent_session":{"value":"%s"}}' "$@"; }
pane_row() { printf '{"pane_id":"%s","label":"%s"}' "$@"; }
echo "{\"result\":{\"agents\":[$(agent_row w5:p1 LEAD '' s-lead),$(agent_row w5:p2 NEAR spawn-LEAD-1 s-near)]}}" >"$T/fake/default-agents.json"
echo "{\"result\":{\"panes\":[$(pane_row w5:p1 '🟢 👑 리드'),$(pane_row w5:p2 '🟢 🫡 옆 칸')]}}" >"$T/fake/default-panes.json"
# The same pane id as a pane on default: each server's names come from its own list.
echo "{\"result\":{\"agents\":[$(agent_row w5:p2 FAR spawn-LEAD-2 s-far),$(agent_row w1:p3 OTHER spawn-ELSE-1 s-other)]}}" >"$T/fake/workers-agents.json"
echo "{\"result\":{\"panes\":[$(pane_row w5:p2 '🔴 🫡 숨은 칸'),$(pane_row w1:p3 '🟢 🫡 남의 칸')]}}" >"$T/fake/workers-panes.json"
mkdir -p "$T/hud/statusline/task-progress"
for s in near far other; do printf '0\tt\t단계 %s\n' "$s" >"$T/hud/statusline/task-progress/s-$s.tsv"; done
lead_input() { printf '{"session_id":"%s","model":{"display_name":"Opus"},"workspace":{"current_dir":"%s"}}' "$1" "$T"; }
run_lead() { lead_input "$1" | env -u HERDR_SOCKET_PATH PATH="$T/fake:$PATH" HERDR_PANE_ID=w5:p1 CLAUDE_CONFIG_DIR="$T/hud" ${2:+HERDR_SOCKET_PATH="$2"} bash "$SCRIPT" 2>&1; }
out=$(run_lead s-lead)
check "a pane beside the lead shows" "$out" '↳ 옆 칸  단계 near'
check "a pane on workers shows" "$out" '↳ 숨은 칸  단계 far' '남의 칸'
rm -f "$T/fake/calls"
out=$(run_lead s-far /x/sessions/workers/herdr.sock)
[ ! -e "$T/fake/calls" ] && r=once || r=twice
check "a pane on workers reads workers once" "$r" 'once'
mv "$T/fake/workers-agents.json" "$T/fake/gone"
out=$(run_lead s-lead)
check "no workers server: the rest still shows" "$out" '↳ 옆 칸  단계 near' '숨은 칸|error'
mv "$T/fake/gone" "$T/fake/workers-agents.json"
touch "$T/fake/stuck"
started=$SECONDS
out=$(STATUSLINE_WORKERS_LIMIT=1 run_lead s-lead)
check "a stuck workers server: the rest still shows" "$out" '↳ 옆 칸  단계 near' '숨은 칸'
[ $((SECONDS - started)) -lt 10 ] && r=soon || r=late
check "a stuck workers server does not hold the line back" "$r" 'soon'
rm -f "$T/fake/stuck"

[ "$fails" -eq 0 ] && echo "all passed" || { echo "$fails failed"; exit 1; }
