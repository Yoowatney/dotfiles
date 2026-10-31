#!/bin/bash
# Checks statusline-command.sh: the env line sits above the HUD, the PROD warning keeps its red background, and the old
# output comes back when the HUD is missing or prints nothing. Run: bash tools/claude/statusline-command.test.sh
set -u

SCRIPT="$(cd "$(dirname "$0")" && pwd)/statusline-command.sh"
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
INPUT='{"model":{"display_name":"Opus"},"workspace":{"current_dir":"'"$T"'"},"context_window":{"used_percentage":12,"context_window_size":200000}}'
fails=0

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

out=$(run)
check "installed HUD prints its lines" "$out" 'Context' '5h '
check "env line comes first" "$(head -1 <<<"$out")" '👤'

out=$(CLOUDSDK_CORE_PROJECT=test-f06c6 run)
check "PROD warning stays beside the HUD" "$out" 'PROD:test-f06c6'
check "PROD warning keeps its red background" "$out" '41m'
check "HUD still drawn on PROD" "$out" 'Context'

out=$(CLAUDE_CONFIG_DIR="$T/empty" run)
check "missing HUD falls back to the old lines" "$out" 'Opus' 'Context'

fake_hud "$T/broken" 'process.exit(1)'
out=$(CLAUDE_CONFIG_DIR="$T/broken" run)
check "HUD that prints nothing falls back" "$out" 'Opus' 'Context'

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

[ "$fails" -eq 0 ] && echo "all passed" || { echo "$fails failed"; exit 1; }
