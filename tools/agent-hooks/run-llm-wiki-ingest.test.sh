#!/usr/bin/env bash
set -euo pipefail

SUBJECT="$(cd "$(dirname "$0")" && pwd)/run-llm-wiki-ingest.sh"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

assert_eq() {
    local expected="$1"
    local actual="$2"
    local message="$3"
    [[ "$actual" == "$expected" ]] || fail "$message (expected=$expected actual=$actual)"
}

setup_fixture() {
    rm -rf "$SANDBOX/state"
    rm -f "$SANDBOX/runs" "$SANDBOX/stdout" "$SANDBOX/stderr"
    mkdir -p "$SANDBOX/state"
    cat > "$SANDBOX/fake-ingest.sh" <<'EOF'
#!/usr/bin/env bash
echo run >> "$HOME/runs"
echo ingest-output
exit "${FAKE_INGEST_EXIT:-0}"
EOF
    chmod +x "$SANDBOX/fake-ingest.sh"
}

run_hook() {
    HOME="$SANDBOX" \
    LLM_WIKI_STATE_DIR="$SANDBOX/state" \
    LLM_WIKI_INGEST_SCRIPT="$SANDBOX/fake-ingest.sh" \
    LLM_WIKI_COOLDOWN_SEC="${TEST_COOLDOWN_SEC:-3600}" \
    LLM_WIKI_LOCK_STALE_SEC="${TEST_LOCK_STALE_SEC:-7200}" \
    FAKE_INGEST_EXIT="${FAKE_INGEST_EXIT:-0}" \
    "$SUBJECT" > "$SANDBOX/stdout" 2> "$SANDBOX/stderr"
}

setup_fixture
run_hook
assert_eq 1 "$(wc -l < "$SANDBOX/runs" | tr -d ' ')" "first call runs ingest"
assert_eq 0 "$(wc -c < "$SANDBOX/stdout" | tr -d ' ')" "hook stdout stays empty"
[[ -f "$SANDBOX/state/.last-stop-ingest" ]] || fail "first call records cooldown"

run_hook
assert_eq 1 "$(wc -l < "$SANDBOX/runs" | tr -d ' ')" "cooldown suppresses duplicate"

setup_fixture
mkdir "$SANDBOX/state/.stop-ingest.lock"
run_hook
[[ ! -f "$SANDBOX/runs" ]] || fail "active lock suppresses ingest"

setup_fixture
mkdir "$SANDBOX/state/.stop-ingest.lock"
touch -t 202001010000 "$SANDBOX/state/.stop-ingest.lock"
TEST_LOCK_STALE_SEC=1 TEST_COOLDOWN_SEC=0 run_hook
assert_eq 1 "$(wc -l < "$SANDBOX/runs" | tr -d ' ')" "stale lock is recovered"
[[ ! -d "$SANDBOX/state/.stop-ingest.lock" ]] || fail "lock is removed after run"

setup_fixture
FAKE_INGEST_EXIT=23 run_hook
assert_eq 1 "$(wc -l < "$SANDBOX/runs" | tr -d ' ')" "failing ingest is attempted once"
assert_eq 0 "$(wc -c < "$SANDBOX/stdout" | tr -d ' ')" "failing ingest does not leak stdout"

setup_fixture
rm "$SANDBOX/fake-ingest.sh"
run_hook
[[ ! -f "$SANDBOX/runs" ]] || fail "missing ingest script is ignored"

echo "OK — run-llm-wiki-ingest checks passed"
