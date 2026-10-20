#!/usr/bin/env bash
# Self-check for llm-wiki-ingest.sh — temp HOME, fake claude, no real vault.
set -euo pipefail

SUBJECT="$(cd "$(dirname "$0")" && pwd)/llm-wiki-ingest.sh"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }
assert_eq() { [[ "$2" == "$1" ]] || fail "$3 (expected=$1 actual=$2)"; }
# Number of lines in $1 matching grep pattern $2 (0 if the file is missing).
hits() { grep -c -- "$2" "$1" 2>/dev/null || true; }

cat > "$SANDBOX/fake-claude" <<'EOF'
#!/usr/bin/env bash
# $2 is the prompt, which the test config makes equal to the source path.
echo "$2" >> "$HOME/calls"
[[ "$2" == */raw/* && "${FAIL_PAGES:-0}" == 1 ]] && exit 1
exit 0
EOF
chmod +x "$SANDBOX/fake-claude"

cat > "$SANDBOX/config.sh" <<EOF
VAULT="$SANDBOX/vault"
llm_wiki_prompt_worklog() { echo "\$1"; }
llm_wiki_prompt_extract() { echo "extract"; }
llm_wiki_prompt_page() { echo "\$1"; }
EOF

new_home() {
    export HOME="$SANDBOX/home-$1"
    mkdir -p "$HOME/.local/share/llm-wiki/raw" "$HOME/.claude/projects/-proj" "$SANDBOX/vault/work-logs"
    : > "$HOME/calls"
}

run() {
    LLM_WIKI_CONFIG="$SANDBOX/config.sh" CLAUDE_BIN="$SANDBOX/fake-claude" LLM_WIKI_NOTIFY=0 \
        "$SUBJECT" > /dev/null 2>&1
}

# Write a transcript with 5 user turns and the given entrypoint, mtime far in the past.
transcript() {
    local f="$HOME/.claude/projects/-proj/$1.jsonl"
    for _ in 1 2 3 4 5; do
        echo "{\"type\":\"user\",\"entrypoint\":\"$2\",\"timestamp\":\"2026-01-01T00:00:00Z\"}" >> "$f"
    done
    touch -t 202001010000 "$f"
}

# Phase 1: headless transcripts are skipped, interactive ones processed.
new_home p1
transcript sdk-one sdk-cli
transcript cli-one cli
run
assert_eq 0 "$(hits "$HOME/calls" sdk-one)" "sdk-cli transcript never reaches claude"
assert_eq 1 "$(hits "$HOME/calls" cli-one)" "cli transcript is processed"
run
assert_eq 1 "$(hits "$HOME/calls" cli-one)" "processed transcript is not rerun"

# Phase 2: an always-failing source is attempted 3 times over 4 runs, GAVE UP logged once.
new_home fail
echo x > "$HOME/.local/share/llm-wiki/raw/bad.md"
for _ in 1 2 3 4; do FAIL_PAGES=1 run; done
assert_eq 3 "$(hits "$HOME/calls" bad.md)" "failing source stops at 3 attempts"
assert_eq 1 "$(hits "$HOME/.local/share/llm-wiki/ingest.log" 'GAVE UP')" "GAVE UP logged once"

# Phase 2: at most 5 sources per run.
new_home cap
for i in 1 2 3 4 5 6 7; do echo x > "$HOME/.local/share/llm-wiki/raw/s$i.md"; done
run
assert_eq 5 "$(hits "$HOME/calls" '/raw/')" "first run handles 5 sources"
run
assert_eq 7 "$(hits "$HOME/calls" '/raw/')" "second run handles the remaining 2"

echo "OK — llm-wiki-ingest checks passed"
