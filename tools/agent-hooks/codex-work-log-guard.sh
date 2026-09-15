#!/usr/bin/env bash
set -uo pipefail

MODE="${1:-}"
STATE_DIR="${LLM_WIKI_STATE_DIR:-$HOME/.local/share/llm-wiki}"
CONFIG="${LLM_WIKI_CONFIG:-$HOME/.config/llm-wiki/config.sh}"
CLAUDE_BIN="${CLAUDE_BIN:-$HOME/.local/bin/claude}"
CLAUDE_TIMEOUT="${CLAUDE_TIMEOUT:-600}"
TIMEOUT_BIN="${LLM_WIKI_TIMEOUT_BIN:-timeout}"
MAX_CAPTURE_BYTES="${CODEX_TURN_MAX_BYTES:-2097152}"
LOG_FILE="$STATE_DIR/ingest.log"

log_guard() {
    mkdir -p "$STATE_DIR" 2>/dev/null || return 0
    printf '[%s] Codex work-log guard: %s\n' \
        "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG_FILE" 2>/dev/null || true
}

file_size() {
    stat -f %z "$1" 2>/dev/null || printf '0'
}

project_from_cwd() {
    local cwd="$1"
    local remainder
    if [[ "$cwd" == */work/* ]]; then
        remainder="${cwd#*/work/}"
        printf '%s' "${remainder%%/*}"
    else
        printf 'personal'
    fi
}

build_recovery_prompt() {
    local turn_file="$1"
    local worklog="$2"
    local project="$3"
    local today="$4"

    cat <<EOF
Review one Codex turn and decide whether it needs a daily work-log entry.

Turn capture file: $turn_file
Existing daily work-log: $worklog
Project: $project
Date: $today

Return exactly NO_WORK when the turn only contains typo, lint, or formatting fixes; package version bumps only; a one-line config change; research or exploration with no conclusion or action; or ordinary conversation with no completed non-trivial work.

Otherwise return exactly one Korean Markdown entry and no code fence or commentary:
## [Brief title]

- **프로젝트:** $project
- **상황:** [problem or trigger]
- **판단:** [decision and rationale]
- **결과:** [outcome or current status]
- **PR/참고:** [link or 없음]

Focus on why and the decision, not file-level changes. Read the turn capture and existing work-log before deciding. Do not duplicate an entry already present in the existing work-log.
EOF
}

payload="$(cat)"
if ! printf '%s' "$payload" | jq -e 'type == "object"' >/dev/null 2>&1; then
    exit 0
fi

session_id="$(printf '%s' "$payload" | jq -r '.session_id // empty' 2>/dev/null)"
safe_session="$(printf '%s' "$session_id" | tr -cd 'A-Za-z0-9._-')"
if [[ -z "$session_id" || -z "$safe_session" || "$safe_session" != "$session_id" ]]; then
    exit 0
fi

if [[ ! -r "$CONFIG" ]]; then
    log_guard "config is not readable: $CONFIG"
    exit 0
fi

# shellcheck source=/dev/null
source "$CONFIG"
if [[ -z "${VAULT:-}" ]]; then
    log_guard "config does not define VAULT"
    exit 0
fi

TURN_STATE_DIR="$STATE_DIR/codex-turns"
STATE_FILE="$TURN_STATE_DIR/$safe_session.json"
TODAY="${LLM_WIKI_TODAY:-$(date '+%Y-%m-%d')}"

case "$MODE" in
    begin)
        transcript_path="$(printf '%s' "$payload" | jq -r '.transcript_path // empty' 2>/dev/null)"
        cwd="$(printf '%s' "$payload" | jq -r '.cwd // empty' 2>/dev/null)"
        worklog="$VAULT/work-logs/$TODAY.md"
        transcript_size="$(file_size "$transcript_path")"
        worklog_size="$(file_size "$worklog")"

        mkdir -p "$TURN_STATE_DIR" 2>/dev/null || exit 0
        state_tmp="$(mktemp "$TURN_STATE_DIR/.state.XXXXXX")" || exit 0
        if jq -n \
            --arg session_id "$session_id" \
            --arg transcript_path "$transcript_path" \
            --arg cwd "$cwd" \
            --arg worklog "$worklog" \
            --arg today "$TODAY" \
            --argjson transcript_size "$transcript_size" \
            --argjson worklog_size "$worklog_size" \
            '{session_id:$session_id,transcript_path:$transcript_path,cwd:$cwd,worklog:$worklog,today:$today,transcript_size:$transcript_size,worklog_size:$worklog_size}' \
            > "$state_tmp" 2>/dev/null; then
            mv "$state_tmp" "$STATE_FILE" 2>/dev/null || rm -f "$state_tmp"
        else
            rm -f "$state_tmp"
        fi
        ;;

    finish)
        if [[ ! -r "$STATE_FILE" ]]; then
            log_guard "no baseline for session $session_id"
            exit 0
        fi

        turn_id="$(printf '%s' "$payload" | jq -r '.turn_id // empty' 2>/dev/null)"
        safe_turn="$(printf '%s' "$turn_id" | tr -cd 'A-Za-z0-9._-')"
        [[ -n "$safe_turn" ]] || safe_turn="unknown"
        claimed_state="$TURN_STATE_DIR/.processing-$safe_session-$safe_turn-$$.json"
        if ! mv "$STATE_FILE" "$claimed_state" 2>/dev/null; then
            log_guard "baseline was already claimed for session $session_id"
            exit 0
        fi

        worklog="$(jq -r '.worklog // empty' "$claimed_state" 2>/dev/null)"
        baseline_worklog_size="$(jq -r '.worklog_size // 0' "$claimed_state" 2>/dev/null)"
        current_worklog_size="$(file_size "$worklog")"
        if (( current_worklog_size > baseline_worklog_size )); then
            log_guard "work-log already updated for session $session_id"
            rm -f "$claimed_state"
            exit 0
        fi

        transcript_path="$(printf '%s' "$payload" | jq -r '.transcript_path // empty' 2>/dev/null)"
        if [[ -z "$transcript_path" ]]; then
            transcript_path="$(jq -r '.transcript_path // empty' "$claimed_state" 2>/dev/null)"
        fi
        transcript_size="$(jq -r '.transcript_size // 0' "$claimed_state" 2>/dev/null)"
        cwd="$(printf '%s' "$payload" | jq -r '.cwd // empty' 2>/dev/null)"
        if [[ -z "$cwd" ]]; then
            cwd="$(jq -r '.cwd // empty' "$claimed_state" 2>/dev/null)"
        fi
        today="$(jq -r '.today // empty' "$claimed_state" 2>/dev/null)"
        [[ -n "$today" ]] || today="$TODAY"
        last_message="$(printf '%s' "$payload" | jq -r '.last_assistant_message // empty' 2>/dev/null)"
        project="$(project_from_cwd "$cwd")"

        mkdir -p "$TURN_STATE_DIR" 2>/dev/null || exit 0
        turn_file="$(mktemp "$TURN_STATE_DIR/.turn.XXXXXX")" || exit 0
        result_file="$(mktemp "$TURN_STATE_DIR/.result.XXXXXX")" || {
            rm -f "$turn_file"
            exit 0
        }
        cleanup_files() {
            rm -f "$turn_file" "$result_file" "$claimed_state"
        }
        trap cleanup_files EXIT INT TERM

        if [[ -r "$transcript_path" ]]; then
            start_byte=$(( transcript_size + 1 ))
            tail -c "+$start_byte" "$transcript_path" 2>/dev/null \
                | tail -c "$MAX_CAPTURE_BYTES" > "$turn_file" 2>/dev/null || true
        else
            printf 'Transcript unavailable.\n' > "$turn_file"
        fi
        if [[ -n "$last_message" ]]; then
            printf '\nLast assistant message:\n%s\n' "$last_message" >> "$turn_file"
        fi

        prompt="$(build_recovery_prompt "$turn_file" "$worklog" "$project" "$today")"
        if "$TIMEOUT_BIN" "$CLAUDE_TIMEOUT" "$CLAUDE_BIN" -p "$prompt" \
            --permission-mode bypassPermissions --no-session-persistence \
            < /dev/null > "$result_file" 2>> "$LOG_FILE"; then
            if grep -Fxq 'NO_WORK' "$result_file" 2>/dev/null; then
                log_guard "no non-trivial missing work for session $session_id"
            elif head -1 "$result_file" | grep -q '^## ' \
                && grep -q '^- \*\*프로젝트:\*\*' "$result_file" \
                && grep -q '^- \*\*상황:\*\*' "$result_file" \
                && grep -q '^- \*\*판단:\*\*' "$result_file" \
                && grep -q '^- \*\*결과:\*\*' "$result_file" \
                && grep -q '^- \*\*PR/참고:\*\*' "$result_file"; then
                mkdir -p "$(dirname "$worklog")" 2>/dev/null || exit 0
                if [[ ! -s "$worklog" ]]; then
                    printf '# %s Work Log\n' "$today" > "$worklog"
                fi
                printf '\n' >> "$worklog"
                cat "$result_file" >> "$worklog"
                printf '\n' >> "$worklog"
                log_guard "recovered missing work-log entry for session $session_id"
            else
                log_guard "ignored invalid recovery output for session $session_id"
            fi
        else
            log_guard "recovery model failed for session $session_id"
        fi

        rm -f "$claimed_state"
        ;;

    *)
        exit 0
        ;;
esac

exit 0
