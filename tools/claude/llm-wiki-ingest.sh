#!/usr/bin/env bash
# ingest.sh — LLM Wiki auto-ingest script
# Triggered by Claude Code and Codex internal Stop hooks. Three phases:
#   Phase 1:   Claude Code session transcripts → work-log entries
#   Phase 1.5: Work-log → raw wiki extraction (architecture decisions, insights)
#   Phase 2:   Raw files (youtube/articles/work-insights) → wiki pages
set -uo pipefail

# ── Constants ────────────────────────────────────────────────────────
LLM_WIKI="$HOME/.local/share/llm-wiki"
# Site-specific config: vault path and the prompt text. Kept out of this repo
# because the prompts cite real internal findings as examples.
LLM_WIKI_CONFIG="${LLM_WIKI_CONFIG:-$HOME/.config/llm-wiki/config.sh}"
if [[ ! -r "$LLM_WIKI_CONFIG" ]]; then
    echo "llm-wiki: config not readable at $LLM_WIKI_CONFIG" >&2
    exit 1
fi
# shellcheck source=/dev/null
source "$LLM_WIKI_CONFIG"
: "${VAULT:?llm-wiki: config must define VAULT}"
CLAUDE_PROJECTS="$HOME/.claude/projects"
WORKLOG_DIR="$VAULT/work-logs"
MANIFEST="$LLM_WIKI/.manifest.json"
LOGFILE="$LLM_WIKI/ingest.log"
LAST_CHECK="$LLM_WIKI/.last-worklog-check"
CLAUDE_BIN="${CLAUDE_BIN:-$HOME/.local/bin/claude}"
# Hard ceiling per claude call. A hung call used to stall the whole run
# indefinitely and prevented later hook runs from making progress.
CLAUDE_TIMEOUT="${CLAUDE_TIMEOUT:-600}"
# Phase 2 page builds are slower: measured p50 367 s, p90 564 s, so 600 s cut off
# normal runs. 5 pages x 1200 s stays under the runner's 7200 s stale-lock window.
CLAUDE_TIMEOUT_PAGE="${CLAUDE_TIMEOUT_PAGE:-1200}"
# Stop retrying an item after this many failures (counted per manifest key).
LLM_WIKI_MAX_FAILS="${LLM_WIKI_MAX_FAILS:-3}"


# ── Logging ──────────────────────────────────────────────────────────
log() {
    local ts
    ts="$(date '+%Y-%m-%d %H:%M:%S')"
    echo "[$ts] $*" >> "$LOGFILE"
}

# ── Manifest helpers ─────────────────────────────────────────────────
# Read a key from the manifest. Returns "true" if key exists, empty otherwise.
is_processed() {
    local key="$1"
    jq -r --arg k "$key" 'if has($k) then "true" else "" end' "$MANIFEST" 2>/dev/null
}

# Set a key to a JSON value in the manifest (atomic write via tmp+mv).
manifest_set() {
    local key="$1" value="$2"
    local tmp
    tmp="$(mktemp "$LLM_WIKI/.manifest.tmp.XXXXXX")"
    jq --arg k "$key" --argjson v "$value" '. + {($k): $v}' "$MANIFEST" > "$tmp" 2>/dev/null \
        && mv "$tmp" "$MANIFEST"
    # Clean up tmp on failure
    [[ -f "$tmp" ]] && rm -f "$tmp"
}

mark_processed() { manifest_set "$1" true; }

# Failure counts live in the manifest as "fail:<key>" so a source that always
# fails stops being retried every cycle.
too_many_fails() {
    local n
    n="$(jq -r --arg k "fail:$1" '.[$k] // 0' "$MANIFEST" 2>/dev/null)"
    [[ "${n:-0}" -ge "$LLM_WIKI_MAX_FAILS" ]]
}

# Bump the failure count; on reaching the cap log GAVE UP and notify once.
record_failure() {
    local key="$1" name="$2" n
    n="$(jq -r --arg k "fail:$key" '(.[$k] // 0) + 1' "$MANIFEST" 2>/dev/null)"
    manifest_set "fail:$key" "${n:-1}"
    if [[ "${n:-1}" -ge "$LLM_WIKI_MAX_FAILS" ]]; then
        log "  GAVE UP on $name after $n failures (no more retries)"
        [[ "${LLM_WIKI_NOTIFY:-1}" == "0" ]] || \
            osascript -e "display notification \"gave up on $name\" with title \"llm-wiki\"" >/dev/null 2>&1 || true
    fi
}

# ── Helper: count user turns in a JSONL session file ─────────────────
count_user_turns() {
    local file="$1"
    jq -r 'select(.type == "user") | .type' "$file" 2>/dev/null | wc -l | tr -d ' '
}

# ── Helper: infer project name from session file path ────────────────
# Claude encodes a project's path as its directory name, with "/" turned into
# "-": $HOME/work/foo becomes -Users-me-work-foo. Derive the prefix from $HOME
# rather than hardcoding it, so this works on any account.
project_from_path() {
    local filepath="$1"
    local dir_name
    dir_name="$(basename "$(dirname "$filepath")")"

    local work="${HOME//\//-}-work"

    case "$dir_name" in
        "$work")
            echo "general"
            ;;
        "$work"-*)
            # -Users-me-work-foo-bar-baz → foo
            local segment
            segment="${dir_name#"$work"-}"
            echo "${segment%%-*}"
            ;;
        *)
            # Fallback: last meaningful segment
            local last
            last="${dir_name##*-}"
            echo "$last"
            ;;
    esac
}

# ── Phase 1: Work-log ingest ────────────────────────────────────────
phase1_worklog() {
    log "Phase 1: Scanning for session transcripts..."
    local count=0
    local processed=0
    local max_per_run=10  # Limit API calls per run to control cost/time

    while IFS= read -r session_file; do
        # Skip files in subdirectories (subagents, vercel-plugin, memory, etc.)
        # We only want .jsonl files directly inside a project directory
        local rel_path="${session_file#"$CLAUDE_PROJECTS"/}"
        # rel_path looks like: -Users-yoyoo-work/abc123.jsonl
        # Count slashes — should be exactly 1 (projdir/file.jsonl)
        local slash_count
        slash_count="$(echo "$rel_path" | tr -cd '/' | wc -c | tr -d ' ')"
        if [[ "$slash_count" -ne 1 ]]; then
            continue
        fi

        local filename
        filename="$(basename "$session_file")"
        # The private config may list sessions that must never be read.
        if [[ " ${LLM_WIKI_SKIP_SESSIONS[*]:-} " == *" ${filename%.jsonl} "* ]]; then
            continue
        fi
        local file_size
        file_size="$(stat -f %z "$session_file")"
        local manifest_key="worklog:${filename}:${file_size}"

        count=$((count + 1))

        # Skip active sessions — file modified within last 10 minutes is likely still in use
        # (lsof doesn't work because Claude opens/writes/closes the file each message)
        local file_age_min
        file_age_min=$(( ( $(date +%s) - $(stat -f %m "$session_file") ) / 60 ))
        if [[ "$file_age_min" -lt 10 ]]; then
            log "  Skipping $filename — modified ${file_age_min}m ago (likely active)"
            continue
        fi

        # Skip if already processed at this size
        if [[ -n "$(is_processed "$manifest_key")" ]]; then
            continue
        fi

        # The private config may also list text that marks a session as private.
        # Unlike session IDs, this survives /clear starting a new transcript.
        local pattern skip=""
        for pattern in "${LLM_WIKI_SKIP_PATTERNS[@]:-}"; do
            [[ -n "$pattern" ]] && grep -qF -- "$pattern" "$session_file" 2>/dev/null && skip=1 && break
        done
        if [[ -n "$skip" ]]; then
            mark_processed "$manifest_key"
            continue
        fi

        # Headless runs (launchd bots, `claude -p`) are noise, not Yoyoo's work.
        # The transcript stays on disk; only this phase skips it.
        if [[ "$(grep -m1 -o '"entrypoint":"[^"]*"' "$session_file" 2>/dev/null)" == '"entrypoint":"sdk'* ]]; then
            mark_processed "$manifest_key"
            continue
        fi

        too_many_fails "$manifest_key" && continue

        # Count user turns
        local turns
        turns="$(count_user_turns "$session_file")"
        if [[ "$turns" -lt 5 ]]; then
            log "  Skipping $filename — only $turns user turns (too trivial)"
            mark_processed "$manifest_key"
            continue
        fi

        # Infer project
        local project
        project="$(project_from_path "$session_file")"
        # Use session date (first user message timestamp), fall back to file mtime
        local session_date
        session_date="$(jq -r 'select(.type == "user") | .timestamp // empty' "$session_file" 2>/dev/null | head -1 | cut -c1-10)"
        if [[ -z "$session_date" || "$session_date" == "null" ]]; then
            session_date="$(date -r "$(stat -f %m "$session_file")" '+%Y-%m-%d')"
        fi
        local worklog_file="$WORKLOG_DIR/${session_date}.md"

        # Respect per-run limit
        if [[ "$processed" -ge "$max_per_run" ]]; then
            log "  Reached max_per_run=$max_per_run, deferring rest to next cycle"
            break
        fi

        log "  Processing $filename ($turns user turns, project=$project)..."

        local prompt
        prompt="$(llm_wiki_prompt_worklog "$session_file" "$project" "$session_date" "$worklog_file")"

        # Run claude -p directly (< /dev/null prevents it from consuming the while-read stdin)
        local outfile="$LLM_WIKI/.tmp-worklog-output-$$.txt"
        if timeout "$CLAUDE_TIMEOUT" "$CLAUDE_BIN" -p "$prompt" --permission-mode bypassPermissions --no-session-persistence < /dev/null > "$outfile" 2>&1; then
            if grep -q "NO_WORK" "$outfile" 2>/dev/null; then
                log "  No non-trivial work found in $filename"
            else
                log "  Work-log entries written for $filename"
            fi
            mark_processed "$manifest_key"
            processed=$((processed + 1))
        else
            log "  FAILED processing $filename (exit=$?; 124=timeout after ${CLAUDE_TIMEOUT}s). Will retry next cycle."
            record_failure "$manifest_key" "$filename"
        fi
        rm -f "$outfile"
    done < <(find "$CLAUDE_PROJECTS" -name "*.jsonl" -type f 2>/dev/null)

    # Update the last-check timestamp
    touch "$LAST_CHECK"
    log "Phase 1 complete: scanned=$count, processed=$processed"
}

# ── Phase 1.5: Work-log → Raw wiki extraction ─────────────────────
phase15_worklog_to_raw() {
    log "Phase 1.5: Scanning work-logs for wiki-worthy content..."
    local today
    today="$(date '+%Y-%m-%d')"
    local worklog_file="$WORKLOG_DIR/${today}.md"

    # Only process today's work-log (freshly written by Phase 1)
    if [[ ! -f "$worklog_file" ]]; then
        log "  No work-log for today ($today), skipping"
        return
    fi

    local content_hash
    content_hash="$(md5 -q "$worklog_file")"
    local manifest_key="wiki-extract:${today}:${content_hash}"
    if [[ -n "$(is_processed "$manifest_key")" ]]; then
        log "  Already extracted wiki content for $today (hash=$content_hash), skipping"
        return
    fi
    too_many_fails "$manifest_key" && return

    log "  Analyzing $today work-log for wiki-worthy content..."

    local raw_dir="$LLM_WIKI/raw/work-insights"
    mkdir -p "$raw_dir"

    local prompt
    prompt="$(llm_wiki_prompt_extract "$worklog_file" "$today" "$raw_dir")"

    local outfile="$LLM_WIKI/.tmp-wiki-extract-$$.txt"
    if timeout "$CLAUDE_TIMEOUT" "$CLAUDE_BIN" -p "$prompt" --permission-mode bypassPermissions --no-session-persistence > "$outfile" 2>&1; then
        if grep -q "NO_WIKI_CONTENT" "$outfile" 2>/dev/null; then
            log "  No wiki-worthy content in today's work-log"
        else
            local new_files
            new_files=$(find "$raw_dir" -name "${today}-*" -type f 2>/dev/null | wc -l | tr -d ' ')
            log "  Extracted $new_files wiki-worthy files from work-log"
        fi
        mark_processed "$manifest_key"
    else
        log "  FAILED extracting wiki content from work-log (exit=$?; 124=timeout after ${CLAUDE_TIMEOUT}s). Will retry next cycle."
        record_failure "$manifest_key" "work-log $today"
    fi
    rm -f "$outfile"
}

# ── Phase 2: Wiki ingest ────────────────────────────────────────────
phase2_wiki() {
    log "Phase 2: Scanning for raw wiki sources..."
    local count=0
    local processed=0
    local attempted=0
    local max_per_run=5  # Attempts, not successes: bounds the run to 5 x CLAUDE_TIMEOUT_PAGE

    while IFS= read -r raw_file; do
        local filename
        filename="$(basename "$raw_file")"
        local manifest_key="wiki:${filename}"

        count=$((count + 1))

        # Skip if already processed
        if [[ -n "$(is_processed "$manifest_key")" ]]; then
            continue
        fi
        too_many_fails "$manifest_key" && continue

        if [[ "$attempted" -ge "$max_per_run" ]]; then
            log "  Reached max_per_run=$max_per_run, deferring rest to next cycle"
            break
        fi
        attempted=$((attempted + 1))

        log "  Processing wiki source: $filename..."

        local prompt
        prompt="$(llm_wiki_prompt_page "$raw_file")"

        # Run claude -p directly (< /dev/null prevents it from consuming the while-read stdin)
        local outfile="$LLM_WIKI/.tmp-wiki-output-$$.txt"
        if timeout "$CLAUDE_TIMEOUT_PAGE" "$CLAUDE_BIN" -p "$prompt" --permission-mode bypassPermissions --no-session-persistence < /dev/null > "$outfile" 2>&1; then
            log "  Wiki page created/updated for $filename"
            mark_processed "$manifest_key"
            processed=$((processed + 1))
        else
            log "  FAILED processing wiki source $filename (exit=$?; 124=timeout after ${CLAUDE_TIMEOUT_PAGE}s). Will retry next cycle."
            record_failure "$manifest_key" "$filename"
        fi
        rm -f "$outfile"
    done < <(find "$LLM_WIKI/raw" -name "*.md" -type f 2>/dev/null)

    log "Phase 2 complete: scanned=$count, processed=$processed"
}

# ── Main ─────────────────────────────────────────────────────────────
main() {
    log "=== Ingest run started ==="

    # Ensure manifest exists
    if [[ ! -f "$MANIFEST" ]]; then
        echo '{}' > "$MANIFEST"
    fi

    phase1_worklog
    phase15_worklog_to_raw
    phase2_wiki

    log "=== Ingest run finished ==="
}

# Run main only if script is executed (not sourced)
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main
fi
