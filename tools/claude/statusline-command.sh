#!/bin/bash
# Claude Code Statusline: Firebase/gcloud env check above Claude HUD (jarrodwatts/claude-hud)
# Without the HUD it falls back to: dir (branch) 👤 user@email.com / Model │ 5h N% │ 7d N%
# Firebase/gcloud projects appear only when they point at PROD or are not set up.

# Read input from stdin
input=$(cat)

# Claude Code paints the whole line gray (38;5;246), so every part sets its own color
C_DIR=$'\033[1;36m'
C_BRANCH=$'\033[0;35m'
C_ACCOUNT=$'\033[0;34m'
C_WARN=$'\033[1;33m'
C_PROD=$'\033[1;97;41m'
C_MODEL=$'\033[1;39m'
C_LABEL=$'\033[0;39m'
C_SEP=$'\033[0;90m'
C_RESET=$'\033[0m'

# 사용률 색: 50% 미만 초록, 80% 미만 노랑, 그 이상 빨강
pct_color() {
	if [ "$1" -ge 80 ]; then
		printf '\033[1;31m'
	elif [ "$1" -ge 50 ]; then
		printf '\033[1;33m'
	else
		printf '\033[1;32m'
	fi
}

# Extract basic info
current_dir="$(echo "$input" | jq -r '.workspace.current_dir // .cwd')"
model="$(echo "$input" | jq -r '.model.display_name')"

# Get git branch
git_branch="$(cd "$current_dir" 2>/dev/null && git -c gc.auto=0 branch --show-current 2>/dev/null || echo '')"

# Build prompt parts
dir_name="$(basename "$current_dir")"

if [ -n "$git_branch" ]; then
	git_info=" ${C_BRANCH}($git_branch)"
else
	git_info=""
fi

# Firebase 로그인 이메일 확인 (캐시: 5분)
FIREBASE_CACHE="/tmp/claude-firebase-login-cache"
FIREBASE_CACHE_MAX_AGE=300

firebase_email=""
if [ -f "$FIREBASE_CACHE" ]; then
	cache_age=$(($(date +%s) - $(stat -f %m "$FIREBASE_CACHE" 2>/dev/null || echo 0)))
	if [ "$cache_age" -lt "$FIREBASE_CACHE_MAX_AGE" ]; then
		firebase_email=$(cat "$FIREBASE_CACHE")
	fi
fi

if [ -z "$firebase_email" ]; then
	firebase_email=$(firebase login:list 2>/dev/null | grep -o '[^ ]*@[^ ]*' | head -1)
	echo "${firebase_email:--}" >"$FIREBASE_CACHE"
fi

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

if [ -n "$firebase_dir" ]; then
	firebase_project=$(cd "$firebase_dir" 2>/dev/null && firebase use 2>/dev/null | head -1)
	[ -z "$firebase_project" ] && firebase_project="-"
else
	firebase_project="-"
fi

# gcloud 프로젝트 확인
gcloud_project=$(gcloud config get-value project 2>/dev/null)
[ -z "$gcloud_project" ] && gcloud_project="?"

# Firebase 표시: 로그인 안 됨 또는 PROD(f06c6)일 때만, dev면 숨김
if [ -z "$firebase_email" ] || [ "$firebase_email" = "-" ]; then
	firebase_info=" ${C_WARN}⚠️ Firebase:로그인필요"
elif echo "$firebase_project" | grep -q "f06c6"; then
	firebase_info=" ${C_PROD} 🔥 PROD:${firebase_project} ${C_RESET}"
else
	firebase_info=""
fi

# gcloud 표시: 미설정 또는 PROD(f06c6)일 때만, dev면 숨김
if [ "$gcloud_project" = "?" ]; then
	gcloud_info=" ${C_WARN}⚠️ gcloud:미설정"
elif echo "$gcloud_project" | grep -q "f06c6"; then
	gcloud_info=" ${C_PROD} ☁️ PROD:${gcloud_project} ${C_RESET}"
else
	gcloud_info=""
fi

# 계정 표시 (gcloud account 또는 firebase email)
if [ -n "$firebase_email" ] && [ "$firebase_email" != "-" ]; then
	account_info=" ${C_ACCOUNT}👤 ${firebase_email}"
else
	gcloud_account=$(gcloud config get-value account 2>/dev/null)
	account_info=" ${C_ACCOUNT}👤 ${gcloud_account:-?}"
fi

# The HUD draws the session lines and has no PROD warning, so the env line stays on top. This runs the launcher from
# the plugin cache because plugin sync installs the plugin on 집맥 but not the copy /claude-hud:setup makes. Any installed
# copy works: the launcher picks the newest version. Node, not bun: bun reads .env and bunfig.toml from the session
# folder, and a bunfig.toml preload would run that repo's code on every refresh.
hud_launchers=("${CLAUDE_CONFIG_DIR:-$HOME/.claude}"/plugins/cache/claude-hud/claude-hud/*/scripts/statusline.mjs)
if [ -f "${hud_launchers[0]}" ]; then
	hud_lines=$(printf '%s' "$input" | node "${hud_launchers[0]}" 2>/dev/null)
	if [ -n "$hud_lines" ]; then
		env_line="${firebase_info}${gcloud_info}${account_info}"
		printf '%s%s\n%s' "${env_line# }" "$C_RESET" "$hud_lines"
		exit 0
	fi
fi

# Function to get API quota from Anthropic OAuth API
get_api_quota() {
	# Get OAuth token from Keychain
	local creds
	creds=$(security find-generic-password -s "Claude Code-credentials" -w 2>/dev/null)
	if [ -z "$creds" ]; then
		echo ""
		return
	fi

	local token
	token=$(echo "$creds" | jq -r '.claudeAiOauth.accessToken' 2>/dev/null)
	if [ -z "$token" ] || [ "$token" = "null" ]; then
		echo ""
		return
	fi

	# Call Anthropic OAuth API
	local response
	response=$(curl -s --max-time 2 "https://api.anthropic.com/api/oauth/usage" \
		-H "Accept: application/json" \
		-H "Content-Type: application/json" \
		-H "User-Agent: claude-code/2.0.76" \
		-H "Authorization: Bearer $token" \
		-H "anthropic-beta: oauth-2025-04-20" 2>/dev/null)

	# Check for error
	if echo "$response" | jq -e '.error' >/dev/null 2>&1; then
		echo ""
		return
	fi

	# Extract utilization values
	local five_hour seven_day
	five_hour=$(echo "$response" | jq -r '.five_hour.utilization // empty' 2>/dev/null)
	seven_day=$(echo "$response" | jq -r '.seven_day.utilization // empty' 2>/dev/null)

	if [ -n "$five_hour" ] && [ -n "$seven_day" ]; then
		# Round to integer (round half up - same as JavaScript Math.round)
		five_hour_int=$(awk "BEGIN {print int($five_hour + 0.5)}" 2>/dev/null || echo "${five_hour%.*}")
		seven_day_int=$(awk "BEGIN {print int($seven_day + 0.5)}" 2>/dev/null || echo "${seven_day%.*}")
		echo "5h:${five_hour_int}% 7d:${seven_day_int}%"
	else
		echo ""
	fi
}

# Try to get API quota (with caching to avoid slow requests)
CACHE_FILE="/tmp/claude-quota-cache"
CACHE_MAX_AGE=60 # Cache for 60 seconds

quota_info=""
if [ -f "$CACHE_FILE" ]; then
	cache_age=$(($(date +%s) - $(stat -f %m "$CACHE_FILE" 2>/dev/null || echo 0)))
	if [ "$cache_age" -lt "$CACHE_MAX_AGE" ]; then
		quota_info=$(cat "$CACHE_FILE")
	fi
fi

if [ -z "$quota_info" ]; then
	quota_info=$(get_api_quota)
	if [ -n "$quota_info" ]; then
		echo "$quota_info" >"$CACHE_FILE"
	fi
fi

# Build status info
sep=" ${C_SEP}│ "
if [[ "$quota_info" =~ ^5h:([0-9]+)%\ 7d:([0-9]+)%$ ]]; then
	five=${BASH_REMATCH[1]}
	seven=${BASH_REMATCH[2]}
	status_info="${C_MODEL}${model}${sep}${C_LABEL}5h $(pct_color "$five")${five}%${sep}${C_LABEL}7d $(pct_color "$seven")${seven}%"
else
	# Fallback to context window usage
	size=$(echo "$input" | jq -r '.context_window.context_window_size // 0')
	usage=$(echo "$input" | jq '.context_window.current_usage')

	ctx_pct=""
	if [ "$size" != "null" ] && [ "$size" != "0" ] && [ -n "$size" ] 2>/dev/null; then
		size_num=$((size))
		if [ "$size_num" -gt 0 ] 2>/dev/null; then
			current=0
			if [ "$usage" != "null" ] && [ -n "$usage" ]; then
				current=$(echo "$usage" | jq -r '(.input_tokens // 0) + (.cache_creation_input_tokens // 0) + (.cache_read_input_tokens // 0)')
			fi
			if [ "$current" != "null" ] && [ "$current" != "0" ] && [ -n "$current" ] 2>/dev/null; then
				current_num=$((current))
				if [ "$current_num" -gt 0 ] 2>/dev/null; then
					pct=$((current_num * 100 / size_num))
					ctx_pct="${sep}${C_LABEL}ctx $(pct_color "$pct")${pct}%"
				fi
			fi
		fi
	fi
	status_info="${C_MODEL}${model}${ctx_pct}"
fi

# Output statusline (2 lines: env on top, model/quota below - keeps quota visible with long branch names)
printf "%s%s:%s%s%s%s\n%s%s" \
	"$C_DIR" "$dir_name" "$git_info" "$firebase_info" "$gcloud_info" "$account_info" "$status_info" "$C_RESET"
