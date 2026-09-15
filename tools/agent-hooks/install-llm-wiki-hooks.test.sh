#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
INSTALLER="$ROOT/install-llm-wiki-hooks.sh"
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
    rm -rf "$SANDBOX/home"
    mkdir -p "$SANDBOX/home/.claude/hooks" "$SANDBOX/home/.codex/hooks"
    printf '#!/bin/bash\necho old-claude\n' > "$SANDBOX/home/.claude/hooks/stop-ingest.sh"
    printf '#!/bin/bash\necho old-codex\n' > "$SANDBOX/home/.codex/hooks/stop-ingest.sh"
    chmod +x "$SANDBOX/home/.claude/hooks/stop-ingest.sh" "$SANDBOX/home/.codex/hooks/stop-ingest.sh"
    cat > "$SANDBOX/home/.codex/hooks.json" <<'EOF'
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "'/tmp/stop-notification.sh'"
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "notify-superset"
          }
        ]
      }
    ]
  }
}
EOF
    chmod 600 "$SANDBOX/home/.codex/hooks.json"
}

run_installer() {
    HOME="$SANDBOX/home" \
    AGENT_HOOKS_ROOT="$ROOT" \
    CLAUDE_HOME="$SANDBOX/home/.claude" \
    CODEX_HOME="$SANDBOX/home/.codex" \
    "$INSTALLER"
}

setup_fixture
run_installer

claude_link="$SANDBOX/home/.claude/hooks/stop-ingest.sh"
codex_link="$SANDBOX/home/.codex/hooks/stop-ingest.sh"
guard_link="$SANDBOX/home/.codex/hooks/work-log-guard.sh"
hooks_json="$SANDBOX/home/.codex/hooks.json"

assert_eq "$ROOT/run-llm-wiki-ingest.sh" "$(readlink "$claude_link")" "Claude Stop uses shared ingest"
assert_eq "$ROOT/codex-stop-ingest.sh" "$(readlink "$codex_link")" "Codex Stop uses recovery orchestrator"
assert_eq "$ROOT/codex-work-log-guard.sh" "$(readlink "$guard_link")" "Codex begin hook uses guard"
grep -Fq 'old-claude' "$claude_link.pre-agent-hooks" || fail "Claude regular wrapper is backed up"
grep -Fq 'old-codex' "$codex_link.pre-agent-hooks" || fail "Codex regular wrapper is backed up"

jq empty "$hooks_json"
assert_eq 600 "$(stat -f %Lp "$hooks_json")" "installer preserves hooks.json mode"
assert_eq 1 "$(jq '[.hooks.UserPromptSubmit[] | select(any(.hooks[]; .command | contains("work-log-guard.sh")))] | length' "$hooks_json")" "guard hook is installed exactly once"
assert_eq 1 "$(jq '[.hooks.UserPromptSubmit[] | select(any(.hooks[]; .command == "notify-superset"))] | length' "$hooks_json")" "existing prompt hook is preserved"
assert_eq 1 "$(jq '[.hooks.Stop[] | select(any(.hooks[]; .command | contains("stop-notification.sh")))] | length' "$hooks_json")" "existing Stop hook is preserved"

first_hash="$(md5 -q "$hooks_json")"
run_installer
second_hash="$(md5 -q "$hooks_json")"
assert_eq "$first_hash" "$second_hash" "second install leaves JSON unchanged"
assert_eq 2 "$(find "$SANDBOX/home" -name '*.pre-agent-hooks' -type f | wc -l | tr -d ' ')" "second install creates no extra backups"

if grep -q 'launchctl' "$INSTALLER"; then
    fail "installer must not invoke launchd"
fi
if grep -q 'Runs every hour via launchd' "$ROOT/../claude/llm-wiki-ingest.sh"; then
    fail "ingest documentation must describe internal hooks"
fi
if [[ -e "$ROOT/../claude/com.yoyoo.wiki-ingest.plist" ]]; then
    fail "obsolete wiki ingest plist must be removed"
fi

echo "OK — install-llm-wiki-hooks checks passed"
