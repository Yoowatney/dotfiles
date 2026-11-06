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

fake_hud "$T/hud" 'console.log("[Opus] Context 12%")'   # a machine without the real HUD plugin must pass too
out=$(CLAUDE_CONFIG_DIR="$T/hud" run)
check "installed HUD prints its lines" "$out" 'Context'
check "HUD is the first line on a dev project" "$(head -1 <<<"$out")" '\[Opus\]'
check "old env line is gone" "$out" 'Context' '👤|로그인필요|미설정'

# The PROD warning was removed (it ran firebase and gcloud on every refresh, over a second each time).
mkdir "$T/bin" "$T/fb"
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
fake_hud "$T/usage" 'console.log("[Opus]\nContext 12% │ Usage Weekly 63% (resets in 1d 10h)")'
reset=$(($(date +%s) + 2 * 86400)) when=$(date -r "$reset" '+%m/%d %H:%M')
codex_record 42.0 "$reset" 60
out=$(COLUMNS=200 CLAUDE_CONFIG_DIR="$T/usage" run | plain)
check "Codex sits at the end of the HUD's usage line" "$(sed -n 2p <<<"$out")" "Usage Weekly 63% \(resets in 1d 10h\) │ Codex 42% · 리셋 $when$"
check "and adds no line of its own" "$(wc -l <<<"$out" | tr -d ' ')" '^2$'
# The usage line is 49 cells and " │ Codex 42% · 리셋 MM/DD HH:MM" 31, with each Hangul two cells wide.
out=$(COLUMNS=80 CLAUDE_CONFIG_DIR="$T/usage" run | plain)
check "80 columns: it still fits on the usage line" "$(sed -n 2p <<<"$out")" "│ Codex 42%"
out=$(COLUMNS=79 CLAUDE_CONFIG_DIR="$T/usage" run | plain)
check "79 columns: the usage line stays as the HUD drew it" "$(sed -n 2p <<<"$out")" '\(resets in 1d 10h\)$'
check "and Codex gets the line under the HUD" "$(sed -n 3p <<<"$out")" "^Codex 42% · 리셋 $when$"
past=$(($(date +%s) - 60))
codex_record 90 "$past" 60
out=$(COLUMNS=200 CLAUDE_CONFIG_DIR="$T/usage" run | plain)
check "a week that has reset since the record: 0%, and when it reset" "$out" "│ Codex 0% · $(date -r "$past" '+%m/%d %H:%M') 리셋됨$"
codex_record 42 "$reset" $((3 * 3600 + 60))
out=$(COLUMNS=200 CLAUDE_CONFIG_DIR="$T/usage" run | plain)
check "a record over an hour old says how old" "$out" "Codex 42% · 리셋 $when · 기록 3시간 전$"
codex_record 42 "$reset" $((2 * 86400 + 60))
out=$(COLUMNS=200 CLAUDE_CONFIG_DIR="$T/usage" run | plain)
check "a record over a day old says it in days" "$out" "Codex 42% · 리셋 $when · 기록 2일 전$"
echo '{"type":"event_msg","payload":{"rate_limits": broken' >"$HOME/.codex/sessions/2026/11/06/rollout-a.jsonl"
out=$(COLUMNS=200 CLAUDE_CONFIG_DIR="$T/usage" run | plain)
check "a broken record: no reading, and the HUD still shows" "$out" '│ Codex 기록 없음$'
check "the HUD's first line is untouched" "$(head -1 <<<"$out")" '^\[Opus\]$'
rm -rf "$HOME/.codex"
out=$(COLUMNS=200 CLAUDE_CONFIG_DIR="$T/usage" run | plain)
check "no Codex records at all: says so" "$out" '│ Codex 기록 없음$'
codex_record 42 "$reset" 60
out=$(COLUMNS=200 CLAUDE_CONFIG_DIR="$T/hud" run | plain)
check "a HUD with no usage line: Codex gets the line under it" "$(sed -n 2p <<<"$out")" "^Codex 42% · 리셋 $when$"
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
