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
check "missing HUD prints no old lines" "$out" '^$' 'Opus|👤'

fake_hud "$T/broken" 'process.exit(1)'
out=$(CLAUDE_CONFIG_DIR="$T/broken" run)
check "HUD that prints nothing leaves no old lines" "$out" '^$' 'Opus|👤'

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
