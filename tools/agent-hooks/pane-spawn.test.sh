#!/usr/bin/env bash
set -euo pipefail

SUBJECT="$(cd "$(dirname "$0")" && pwd)/pane-spawn"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

# A fake herdr that logs each call to $SANDBOX/log. FAIL_START=<n> fails the first n agent starts, SESSIONLESS=1 keeps
# the session id empty as Codex does until its first prompt.
cat > "$SANDBOX/herdr" <<EOF
#!/usr/bin/env bash
echo "\$*" >> "$SANDBOX/log"
case "\$1 \$2" in
  "pane get") jq -n '{result: {pane: {pane_id: "w7:p3", workspace_id: "w7", terminal_id: "term_65f21913323dc21"}}}' ;; # moved: env says w1:p1
  "tab create") jq -n '{result: {root_pane: {pane_id: "w1:new"}, tab: {tab_id: "w1:t9"}}}' ;;
  "pane split") jq -n '{result: {pane: {pane_id: "w1:new"}}}' ;;
  "pane rename"|"agent prompt"|"agent wait"|"pane close") ;;
  "agent start")
    [[ "\$3" =~ ^[a-z][a-z0-9_-]{0,31}\$ ]] || { echo invalid_agent_name; exit 1; } # herdr's rule
    n=\$(cat "$SANDBOX/starts" 2>/dev/null || echo 0); echo \$((n + 1)) > "$SANDBOX/starts"
    [ "\$n" -ge "\${FAIL_START:-0}" ] ;;
  "agent get")
    if [ -n "\${SESSIONLESS:-}" ]; then jq -n '{result: {agent: {interactive_ready: true}}}'
    else jq -n '{result: {agent: {interactive_ready: true, agent_session: {value: "sid"}}}}'; fi ;;
  *) exit 2 ;;
esac
EOF
chmod +x "$SANDBOX/herdr"
printf '#!/bin/sh\necho "pane-label $*" >> "%s/log"\n' "$SANDBOX" > "$SANDBOX/pane-label"
chmod +x "$SANDBOX/pane-label"

ps() { rm -f "$SANDBOX/log" "$SANDBOX/starts"; env PATH="$SANDBOX:$PATH" HERDR_PANE_ID=w1:p1 HERDR_BIN_PATH="$SANDBOX/herdr" PANE_SPAWN_POLL=0 PANE_SPAWN_RETRY_WAIT=0 bash "$SUBJECT" "$@"; }
log() { cat "$SANDBOX/log" 2>/dev/null || true; }
has() { grep -qF -- "$1" "$SANDBOX/log" || fail "$2: no [$1] in: $(log)"; }
hasnt() { if grep -qF -- "$1" "$SANDBOX/log"; then fail "$2: found [$1] in: $(log)"; fi; }
before() { [ "$(grep -nF -- "$1" "$SANDBOX/log" | head -1 | cut -d: -f1)" -lt "$(grep -nF -- "$2" "$SANDBOX/log" | head -1 | cut -d: -f1)" ] || fail "[$1] should come before [$2]: $(log)"; }

# Default: a new tab named for the work; the caller is marked 👑, the new pane 🫡 before its agent starts.
out=$(ps --cwd /work "api#1 설계" "할 일입니다")
has "pane-label --lead" "caller becomes the lead"
has "tab create --workspace w7 --cwd /work --label api#1 설계 --no-focus" "new tab in the caller's current workspace"
has "agent start spawn-65f21913323dc21-" "the agent's name records the caller's terminal, within herdr's limit"
hasnt "pane split" "no split by default"
has "pane rename w1:new 🟢 🫡 api#1 설계" "the first name carries 🫡"
before "pane rename w1:new" "agent start"
before "pane rename w1:new" "pane-label --sync-tabs"
before "pane-label --sync-tabs" "agent start"
has "--kind claude --pane w1:new" "claude by default"
has "agent prompt w1:new 할 일입니다" "instruction delivered"
before "agent get w1:new" "agent prompt"
[[ "$out" == *"w1:new"* ]] || fail "prints the new pane: $out"

# Same work: --same-tab splits the caller's tab.
ps --same-tab "api#1 리뷰" "리뷰" >/dev/null
has "pane split --pane w7:p3 --direction right" "same tab splits the caller's current pane"
hasnt "tab create" "same tab opens no tab"

# Agent arguments pass through, and Codex works without a session id until its first prompt.
SESSIONLESS=1 ps --kind codex "조사" "지시" -- -m gpt-5 >/dev/null
has "--kind codex --pane w1:new --timeout 120000 -- -m gpt-5" "codex with its arguments"
has "agent prompt w1:new 지시" "codex gets the instruction"

# A pane that was not ready for the first start is tried again.
FAIL_START=1 ps "재시도" "지시" >/dev/null
[ "$(cat "$SANDBOX/starts")" = 2 ] || fail "should retry a failed start"
has "agent prompt w1:new 지시" "retry delivers"

# A start that keeps failing removes the empty pane and says so.
if FAIL_START=9 ps "실패" "지시" 2>/dev/null; then fail "a failed start must exit non-zero"; fi
has "pane close w1:new" "the empty pane goes"
hasnt "agent prompt" "nothing to prompt"

# Bad usage.
if ps "이름만" 2>/dev/null; then fail "missing instruction must exit non-zero"; fi
if ps --kind gemini "이름" "지시" 2>/dev/null; then fail "unknown kind must exit non-zero"; fi
if env -u HERDR_PANE_ID HERDR_BIN_PATH="$SANDBOX/herdr" bash "$SUBJECT" "이름" "지시" 2>/dev/null; then fail "outside herdr must exit non-zero"; fi

echo "ok: pane-spawn"
