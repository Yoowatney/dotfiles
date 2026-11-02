#!/bin/bash
# Claude Code Statusline: Claude HUD (jarrodwatts/claude-hud), with a red warning line above it when Firebase or gcloud
# points at PROD. The HUD has no such warning. A dev project prints none.

# Read input from stdin
input=$(cat)

# Claude Code paints the whole line gray (38;5;246), so the warning sets its own color
C_PROD=$'\033[1;97;41m'
C_RESET=$'\033[0m'

current_dir="$(echo "$input" | jq -r '.workspace.current_dir // .cwd')"

# Firebase 프로젝트 확인 (.firebaserc를 현재 및 하위 디렉토리에서 찾기)
firebase_dir=""
search_dir="$current_dir"

if [ -f "${search_dir}/.firebaserc" ]; then
	firebase_dir="$search_dir"
else
	for subdir in "$search_dir"/*/; do
		if [ -f "${subdir}.firebaserc" ]; then
			firebase_dir="${subdir%/}"
			break
		fi
	done
fi

firebase_project=""
if [ -n "$firebase_dir" ]; then
	firebase_project=$(cd "$firebase_dir" 2>/dev/null && firebase use 2>/dev/null | head -1)
fi

# gcloud 프로젝트 확인
gcloud_project=$(gcloud config get-value project 2>/dev/null)

# PROD(f06c6)일 때만 표시, dev면 숨김
if echo "$firebase_project" | grep -q "f06c6"; then
	printf '%s 🔥 PROD:%s %s ' "$C_PROD" "$firebase_project" "$C_RESET"
	prod_shown=1
fi
if echo "$gcloud_project" | grep -q "f06c6"; then
	printf '%s ☁️ PROD:%s %s' "$C_PROD" "$gcloud_project" "$C_RESET"
	prod_shown=1
fi
[ -n "${prod_shown:-}" ] && echo

# This runs the launcher from the plugin cache because plugin sync installs the plugin on 집맥 but not the copy
# /claude-hud:setup makes. Any installed copy works: the launcher picks the newest version. Node, not bun: bun reads
# .env and bunfig.toml from the session folder, and a bunfig.toml preload would run that repo's code on every refresh.
hud_launchers=("${CLAUDE_CONFIG_DIR:-$HOME/.claude}"/plugins/cache/claude-hud/claude-hud/*/scripts/statusline.mjs)
printf '%s' "$input" | node "${hud_launchers[0]}" 2>/dev/null

# Under the HUD: the token-weather mod's forecast for this session (the mod writes it on every reading; a mod cannot
# print into this command's output). Rows are "<cells>\t<ANSI line>", widest first; print the first that fits COLUMNS,
# which Claude Code sets for the status line. No file, or no row that fits: no line.
session_id="$(echo "$input" | jq -r '.session_id // empty')"
weather="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/statusline/token-weather/${session_id}.tsv"
if [ -n "$session_id" ] && [ -f "$weather" ]; then
	awk -F'\t' -v c="${COLUMNS:-0}" 'c == 0 || $1 <= c { print $2; exit }' "$weather"
fi
