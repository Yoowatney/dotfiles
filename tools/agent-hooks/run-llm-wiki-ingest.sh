#!/usr/bin/env bash
set -uo pipefail

STATE_DIR="${LLM_WIKI_STATE_DIR:-$HOME/.local/share/llm-wiki}"
INGEST_SCRIPT="${LLM_WIKI_INGEST_SCRIPT:-$STATE_DIR/ingest.sh}"
COOLDOWN_SEC="${LLM_WIKI_COOLDOWN_SEC:-3600}"
LOCK_STALE_SEC="${LLM_WIKI_LOCK_STALE_SEC:-7200}"
COOLDOWN_FILE="$STATE_DIR/.last-stop-ingest"
LOCK_DIR="$STATE_DIR/.stop-ingest.lock"
LOG_FILE="$STATE_DIR/ingest.log"

mkdir -p "$STATE_DIR" 2>/dev/null || exit 0

now="$(date +%s)"
if [[ -d "$LOCK_DIR" ]]; then
    lock_mtime="$(stat -f %m "$LOCK_DIR" 2>/dev/null || printf '0')"
    if (( now - lock_mtime >= LOCK_STALE_SEC )); then
        rmdir "$LOCK_DIR" 2>/dev/null || exit 0
    fi
fi

mkdir "$LOCK_DIR" 2>/dev/null || exit 0
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT INT TERM

if [[ -f "$COOLDOWN_FILE" ]]; then
    last_run="$(stat -f %m "$COOLDOWN_FILE" 2>/dev/null || printf '0')"
    if (( now - last_run < COOLDOWN_SEC )); then
        exit 0
    fi
fi

if [[ ! -x "$INGEST_SCRIPT" ]]; then
    printf '[%s] Stop hook skipped: ingest script is not executable: %s\n' \
        "$(date '+%Y-%m-%d %H:%M:%S')" "$INGEST_SCRIPT" >> "$LOG_FILE" 2>/dev/null || true
    exit 0
fi

touch "$COOLDOWN_FILE" 2>/dev/null || exit 0
"$INGEST_SCRIPT" >> "$LOG_FILE" 2>&1 || true
exit 0
