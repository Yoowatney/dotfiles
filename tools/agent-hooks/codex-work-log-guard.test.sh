#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
GUARD="$ROOT/codex-work-log-guard.sh"
STOP="$ROOT/codex-stop-ingest.sh"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

assert_contains() {
    local file="$1"
    local expected="$2"
    local message="$3"
    grep -Fq -- "$expected" "$file" || fail "$message"
}

assert_not_contains() {
    local file="$1"
    local unexpected="$2"
    local message="$3"
    if [[ -f "$file" ]] && grep -Fq -- "$unexpected" "$file"; then
        fail "$message"
    fi
}

setup_fixture() {
    rm -rf "$SANDBOX/home"
    mkdir -p "$SANDBOX/home/vault/work-logs" "$SANDBOX/home/state"
    printf 'VAULT="$HOME/vault"\n' > "$SANDBOX/home/config.sh"
    printf 'before turn\n' > "$SANDBOX/home/transcript.jsonl"
    cat > "$SANDBOX/home/fake-claude" <<'EOF'
#!/usr/bin/env bash
echo call >> "$HOME/claude-calls"
prompt="$2"
turn_file="$(printf '%s\n' "$prompt" | sed -n 's/^Turn capture file: //p' | head -1)"
if [[ -n "$turn_file" && -f "$turn_file" ]]; then
    stat -f %z "$turn_file" > "$HOME/captured-size"
fi
cat "$HOME/model-result"
EOF
    chmod +x "$SANDBOX/home/fake-claude"
}

run_guard() {
    local mode="$1"
    HOME="$SANDBOX/home" \
    LLM_WIKI_STATE_DIR="$SANDBOX/home/state" \
    LLM_WIKI_CONFIG="$SANDBOX/home/config.sh" \
    LLM_WIKI_TODAY="2026-09-15" \
    CLAUDE_BIN="$SANDBOX/home/fake-claude" \
    CLAUDE_TIMEOUT=5 \
    "$GUARD" "$mode"
}

begin_payload() {
    jq -cn \
        --arg transcript "$SANDBOX/home/transcript.jsonl" \
        '{session_id:"session-1",transcript_path:$transcript,cwd:"/Users/test/work/munice",hook_event_name:"UserPromptSubmit"}'
}

stop_payload() {
    jq -cn \
        --arg transcript "$SANDBOX/home/transcript.jsonl" \
        '{session_id:"session-1",turn_id:"turn-1",transcript_path:$transcript,cwd:"/Users/test/work/munice",hook_event_name:"Stop",last_assistant_message:"Implemented and verified the feature."}'
}

valid_entry() {
    cat <<'EOF'
## Recovered entry

- **프로젝트:** munice
- **상황:** Codex가 작업을 수행했지만 직접 작업 로그를 남기지 않았다.
- **판단:** 현재 턴만 복구하여 중복 기록을 피했다.
- **결과:** 누락된 작업 기록을 복구했다.
- **PR/참고:** 없음
EOF
}

setup_fixture
begin_output="$(begin_payload | run_guard begin)"
[[ -z "$begin_output" ]] || fail "begin stdout must stay empty"
state_file="$SANDBOX/home/state/codex-turns/session-1.json"
[[ -f "$state_file" ]] || fail "begin creates per-session state"
[[ "$(jq -r '.worklog_size' "$state_file")" == "0" ]] || fail "begin records missing work-log as size zero"
[[ "$(jq -r '.transcript_size' "$state_file")" == "12" ]] || fail "begin records transcript offset"

worklog="$SANDBOX/home/vault/work-logs/2026-09-15.md"
printf '# 2026-09-15 Work Log\n\n## Agent entry\n' > "$worklog"
stop_output="$(stop_payload | run_guard finish)"
[[ -z "$stop_output" ]] || fail "finish stdout must stay empty"
[[ ! -f "$SANDBOX/home/claude-calls" ]] || fail "updated work-log skips recovery model"
assert_not_contains "$worklog" "Recovered entry" "updated work-log must not be duplicated"

setup_fixture
begin_payload | run_guard begin
printf 'non-trivial tool work\n' >> "$SANDBOX/home/transcript.jsonl"
valid_entry > "$SANDBOX/home/model-result"
stop_payload | run_guard finish
assert_contains "$worklog" "# 2026-09-15 Work Log" "recovery creates daily header"
assert_contains "$worklog" "## Recovered entry" "missing work-log is recovered"
[[ "$(wc -l < "$SANDBOX/home/claude-calls" | tr -d ' ')" == "1" ]] || fail "recovery calls model once"
[[ ! -f "$state_file" ]] || fail "finish removes consumed state"

setup_fixture
begin_payload | run_guard begin
printf 'small talk\n' >> "$SANDBOX/home/transcript.jsonl"
printf 'NO_WORK\n' > "$SANDBOX/home/model-result"
stop_payload | run_guard finish
[[ ! -f "$worklog" ]] || fail "NO_WORK does not create work-log"

setup_fixture
begin_payload | run_guard begin
printf 'non-trivial tool work\n' >> "$SANDBOX/home/transcript.jsonl"
printf 'unstructured output\n' > "$SANDBOX/home/model-result"
stop_payload | run_guard finish
[[ ! -f "$worklog" ]] || fail "invalid model output does not create work-log"

setup_fixture
printf 'not-json' | run_guard begin
[[ ! -d "$SANDBOX/home/state/codex-turns" ]] || fail "malformed hook JSON creates no turn state"

setup_fixture
begin_payload | run_guard begin
rm "$SANDBOX/home/transcript.jsonl"
valid_entry > "$SANDBOX/home/model-result"
stop_payload | run_guard finish
assert_contains "$worklog" "## Recovered entry" "last assistant message recovers when transcript is absent"

setup_fixture
begin_payload | run_guard begin
dd if=/dev/zero bs=1048576 count=3 2>/dev/null >> "$SANDBOX/home/transcript.jsonl"
printf 'NO_WORK\n' > "$SANDBOX/home/model-result"
stop_payload | run_guard finish
captured_size="$(cat "$SANDBOX/home/captured-size")"
[[ "$captured_size" -le 2097400 ]] || fail "turn capture is capped near 2 MiB (actual=$captured_size)"

setup_fixture
begin_payload | run_guard begin
printf 'non-trivial tool work\n' >> "$SANDBOX/home/transcript.jsonl"
valid_entry > "$SANDBOX/home/model-result"
cat > "$SANDBOX/home/fake-ingest.sh" <<'EOF'
#!/usr/bin/env bash
echo run >> "$HOME/ingest-runs"
EOF
chmod +x "$SANDBOX/home/fake-ingest.sh"
stop_stdout="$({ stop_payload | HOME="$SANDBOX/home" \
    AGENT_HOOKS_ROOT="$ROOT" \
    LLM_WIKI_STATE_DIR="$SANDBOX/home/state" \
    LLM_WIKI_CONFIG="$SANDBOX/home/config.sh" \
    LLM_WIKI_TODAY="2026-09-15" \
    LLM_WIKI_INGEST_SCRIPT="$SANDBOX/home/fake-ingest.sh" \
    LLM_WIKI_COOLDOWN_SEC=0 \
    CLAUDE_BIN="$SANDBOX/home/fake-claude" \
    CLAUDE_TIMEOUT=5 \
    "$STOP"; })"
[[ -z "$stop_stdout" ]] || fail "Codex Stop orchestrator stdout must stay empty"
assert_contains "$worklog" "## Recovered entry" "Stop orchestrator runs recovery first"
[[ "$(wc -l < "$SANDBOX/home/ingest-runs" | tr -d ' ')" == "1" ]] || fail "Stop orchestrator runs shared ingest once"

echo "OK — codex-work-log-guard checks passed"
