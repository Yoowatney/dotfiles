#!/usr/bin/env bash
# ingest.sh — LLM Wiki auto-ingest script
# Runs every hour via launchd. Three phases:
#   Phase 1:   Claude Code session transcripts → work-log entries
#   Phase 1.5: Work-log → raw wiki extraction (architecture decisions, insights)
#   Phase 2:   Raw files (youtube/articles/work-insights) → wiki pages
set -uo pipefail

# ── Constants ────────────────────────────────────────────────────────
LLM_WIKI="$HOME/.local/share/llm-wiki"
VAULT="$HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents/obsidian-munice"
CLAUDE_PROJECTS="$HOME/.claude/projects"
WORKLOG_DIR="$VAULT/work-logs"
MANIFEST="$LLM_WIKI/.manifest.json"
LOGFILE="$LLM_WIKI/ingest.log"
LAST_CHECK="$LLM_WIKI/.last-worklog-check"
CLAUDE_BIN="${CLAUDE_BIN:-$HOME/.local/bin/claude}"
# Hard ceiling per claude call. A hung call used to stall the whole run
# indefinitely (one sat for four months), and launchd will not start the
# next cycle while the previous one is still alive.
CLAUDE_TIMEOUT="${CLAUDE_TIMEOUT:-600}"


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

# Mark a key as processed in the manifest (atomic write via tmp+mv).
mark_processed() {
    local key="$1"
    local tmp
    tmp="$(mktemp "$LLM_WIKI/.manifest.tmp.XXXXXX")"
    jq --arg k "$key" '. + {($k): true}' "$MANIFEST" > "$tmp" 2>/dev/null \
        && mv "$tmp" "$MANIFEST"
    # Clean up tmp on failure
    [[ -f "$tmp" ]] && rm -f "$tmp"
}

# ── Helper: count user turns in a JSONL session file ─────────────────
count_user_turns() {
    local file="$1"
    jq -r 'select(.type == "user") | .type' "$file" 2>/dev/null | wc -l | tr -d ' '
}

# ── Helper: infer project name from session file path ────────────────
project_from_path() {
    local filepath="$1"
    # Extract the project directory name (parent of the .jsonl file)
    local dir_name
    dir_name="$(basename "$(dirname "$filepath")")"

    case "$dir_name" in
        -Users-yoyoo-work)
            echo "general"
            ;;
        -Users-yoyoo-work-munice|*-Users-yoyoo-work-munice-*)
            echo "munice"
            ;;
        -Users-yoyoo-work-gomgom|*-Users-yoyoo-work-gomgom-*)
            echo "gomgom"
            ;;
        -Users-yoyoo-work-*)
            # Extract the segment after -Users-yoyoo-work-
            local segment
            segment="${dir_name#*-Users-yoyoo-work-}"
            # Take the first segment (before any dash that starts a sub-path)
            segment="${segment%%-*}"
            echo "$segment"
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
        prompt="You are a work-log extraction agent. Your task:

1. Read the transcript file at: $session_file
2. Extract development work entries. Include anything where:
   - Code was written, modified, or deleted
   - A technical decision was made
   - Analysis or investigation was performed and reached a conclusion
   - Infrastructure or configuration was changed
   - A bug was diagnosed and fixed
   When in doubt, INCLUDE the entry. The user prefers too many entries over missed ones.

SKIP ONLY these:
- Pure typo/lint/formatting fixes (no logic change)
- Package version bumps with no other changes
- General chat, stock talk, casual conversation with no work outcome

For each qualifying piece of work, write an entry in Korean using this exact format:

## [Brief title of what was done]

- **프로젝트:** $project
- **상황:** [What was the problem or trigger]
- **판단:** [What decision was made and why — alternatives considered if any]
- **결과:** [Outcome, metrics, or current status]
- **PR/참고:** [PR link or related doc if applicable, or N/A]

Rules:
- The work-log file is: $worklog_file
- If the file does not exist, create it with the header: # $session_date Work Log
- If the file already exists, append new entries at the end (do NOT overwrite)
- Do NOT duplicate entries that already exist in the file (check before appending)
- Focus on WHY and DECISION, not file-level changes
- One entry per logical unit of work
- If there is NO qualifying work at all, output exactly NO_WORK and do not modify any files"

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

    log "  Analyzing $today work-log for wiki-worthy content..."

    local raw_dir="$LLM_WIKI/raw/work-insights"
    mkdir -p "$raw_dir"

    local prompt
    prompt="You are a wiki knowledge extractor. Your task:

1. Read the work-log file at: $worklog_file
2. Identify entries that contain wiki-worthy knowledge:
   - Architecture decisions (e.g. 'chose Firestore over PostgreSQL because...')
   - Troubleshooting insights (e.g. 'DNSSEC stale key caused DNS failure')
   - New patterns or frameworks (e.g. 'introduced Repository pattern')
   - Infrastructure changes (e.g. 'migrated LB to Cloudflare Worker')
   - Non-obvious technical findings (e.g. 'Firestore Read Ops = 25% of total cost')

3. Skip entries that are NOT wiki-worthy:
   - Simple bug fixes with no reusable insight
   - Routine deployments
   - Config changes
   - PR reviews

4. For each wiki-worthy entry, write a summary to a file at:
   $raw_dir/${today}-{topic-in-kebab-case}.md

   Format:
   ---
   source: work-log
   date: $today
   type: work-insight
   project: {munice|gomgom|general}
   ---

   {Detailed knowledge extracted from the work-log entry.
    Focus on the WHY, the DECISION, and the OUTCOME.
    Include technical details that would be useful to reference later.}

5. Create one file per distinct topic. If multiple work-log entries relate to the same topic, merge them into one file.

6. If there is NOTHING wiki-worthy in today's work-log, output exactly NO_WIKI_CONTENT and do not create any files."

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
    fi
    rm -f "$outfile"
}

# ── Phase 2: Wiki ingest ────────────────────────────────────────────
phase2_wiki() {
    log "Phase 2: Scanning for raw wiki sources..."
    local count=0
    local processed=0

    while IFS= read -r raw_file; do
        local filename
        filename="$(basename "$raw_file")"
        local manifest_key="wiki:${filename}"

        count=$((count + 1))

        # Skip if already processed
        if [[ -n "$(is_processed "$manifest_key")" ]]; then
            continue
        fi

        log "  Processing wiki source: $filename..."

        local prompt
        prompt="You are a wiki-page generation agent. Your task:

1. Read the wiki rules at: $VAULT/CLAUDE.md
2. Read the source file at: $raw_file
3. Determine which project this belongs to (munice, gomgom, or general)
4. Read that project's index: $VAULT/wiki/{project}/index.md

Based on the source content:
- Create or update the appropriate wiki page(s) in $VAULT/wiki/
- Follow the directory structure and naming conventions from CLAUDE.md
- Update the relevant project index.md (munice/index.md, gomgom/index.md, or general/index.md)
- Append a timestamped entry to $VAULT/wiki/log.md documenting what you did

Write all wiki content in Korean. Follow the existing style of pages in the wiki."

        # Run claude -p directly (< /dev/null prevents it from consuming the while-read stdin)
        local outfile="$LLM_WIKI/.tmp-wiki-output-$$.txt"
        if timeout "$CLAUDE_TIMEOUT" "$CLAUDE_BIN" -p "$prompt" --permission-mode bypassPermissions --no-session-persistence < /dev/null > "$outfile" 2>&1; then
            log "  Wiki page created/updated for $filename"
            mark_processed "$manifest_key"
            processed=$((processed + 1))
        else
            log "  FAILED processing wiki source $filename (exit=$?; 124=timeout after ${CLAUDE_TIMEOUT}s). Will retry next cycle."
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
