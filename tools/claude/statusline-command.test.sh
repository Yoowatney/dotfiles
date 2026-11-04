#!/bin/bash
# Checks statusline-command.sh: the HUD first, no PROD warning on any project, nothing left of the old status line.
# Run: bash tools/claude/statusline-command.test.sh
set -u

SCRIPT="$(cd "$(dirname "$0")" && pwd)/statusline-command.sh"
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
mkdir -p "$T/home" && export HOME="$T/home"   # a home with no 요유 차례 summary, whatever this machine's holds
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

# The 요유 차례 line: the board items waiting on 요유, read from ~/.config/munice/yoyoo-turn.json (the agent-board watch
# job writes it every 30 minutes), with how old that file is. No file, or a broken one: no line and no error.
turn() {   # <json, or empty for no file> [<COLUMNS>]
	mkdir -p "$T/home/.config/munice"
	if [ -n "$1" ]; then printf '%s' "$1" >"$T/home/.config/munice/yoyoo-turn.json"; else rm -f "$T/home/.config/munice/yoyoo-turn.json"; fi
	HOME="$T/home" CLAUDE_CONFIG_DIR="$T/hud" COLUMNS="${2:-120}" run
}
now=$(date +%s)
out=$(turn '{"at":'$((now - 1800))',"count":3,"overdue":1,"oldestDays":23,"top":"11/01 P2 api#10 지난 일","topUrl":"https://github.com/munice-dev/nightly_api/issues/10","sinceFrom":"label"}')
check "요유 차례: count, past due, longest wait, top item" "$out" '요유 차례 3건.*기한 지남 1건.*최장 23일.*11/01 P2 api#10 지난 일'
check "요유 차례: the file's age in minutes" "$out" '30분 전'
check "요유 차례: under the HUD" "$(head -1 <<<"$out")" '\[Opus\]'
out=$(turn '{"at":'$((now - 3 * 3600 - 120))',"count":0,"overdue":0,"oldestDays":0,"top":null,"topUrl":null,"sinceFrom":"label"}')
check "요유 차례: none waiting, hours of age" "$out" '요유 차례 0건.*3시간 전' '기한 지남|최장'
out=$(turn '{"at":'$((now - 1800))',"count":1,"overdue":0,"oldestDays":2,"top":"P3 cf#20 아주 긴 제목이 들어가면 상태줄 너비에 맞춰 잘려야 한다 그렇지 않으면 줄이 넘어간다","topUrl":"x","sinceFrom":"label"}' 60)
line=$(grep '요유 차례' <<<"$out" | sed $'s/\x1b\\[[0-9;]*m//g')
check "요유 차례: a long title is cut, the age kept" "$line" '…' '넘어간다'
cells=$(python3 -I -c 'import sys,unicodedata; s=sys.stdin.read().rstrip("\n"); print(sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s))' <<<"$line")
check "요유 차례: fits 60 cells" "$([ "${cells:-999}" -le 60 ] && echo fits || echo "$cells cells")" '^fits$'
# A narrow pane drops parts from the end, 최장 first, then 기한 지남; the count and the age always stay (Codex review).
fits() {   # <line> <cells> -> fits | <n> cells
	local n
	n=$(python3 -I -c 'import sys,unicodedata; s=sys.stdin.read().rstrip("\n"); print(sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s))' <<<"$1")
	[ "${n:-999}" -le "$2" ] && echo fits || echo "$n cells"
}
real='{"at":'$((now - 60))',"count":40,"overdue":32,"oldestDays":28,"top":"10/19 P3 api#10 시험용 제목이 이 정도 길이면 좁은 칸에서 잘린다","topUrl":"x","sinceFrom":"label"}'
line=$(turn "$real" 40 | grep '요유 차례' | sed $'s/\x1b\\[[0-9;]*m//g')
check "요유 차례 at 40 cells: count, past due and age kept" "$line" '^요유 차례 40건 · 기한 지남 32건 · 1분 전$'
check "요유 차례 at 40 cells: fits" "$(fits "$line" 40)" '^fits$'
line=$(turn "$real" 30 | grep '요유 차례' | sed $'s/\x1b\\[[0-9;]*m//g')
check "요유 차례 at 30 cells: count and age only" "$line" '^요유 차례 40건 · 1분 전$'

# A summary written ahead of this clock (집맥 runs a little behind 회사맥) reads 0분 전, not -1분 전 (code review).
out=$(turn '{"at":'$((now + 90))',"count":1,"overdue":0,"oldestDays":2,"top":null,"topUrl":null,"sinceFrom":"label"}')
check "요유 차례: a future write time reads 0분 전" "$out" ' · .*0분 전' '-[0-9]'
# A wait counted from the last update (label times unavailable) is marked as approximate.
out=$(turn '{"at":'$((now - 60))',"count":1,"overdue":0,"oldestDays":5,"top":null,"topUrl":null,"sinceFrom":"updatedAt"}')
check "요유 차례: a wait from updatedAt reads 최장 ~5일" "$out" '최장 ~5일'

out=$(turn '')
check "요유 차례: no file, no line" "$out" 'Context' '요유 차례'
out=$(turn 'not json')
check "요유 차례: broken file, no line and no error" "$out" 'Context' '요유 차례|jq|rror'

[ "$fails" -eq 0 ] && echo "all passed" || { echo "$fails failed"; exit 1; }
