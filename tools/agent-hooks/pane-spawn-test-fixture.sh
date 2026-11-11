#!/usr/bin/env bash
set -euo pipefail
# Fixture ancestry must not come from the agent session running this test.
unset HERDR_TASK_ID CODEX_THREAD_ID

SUBJECT="${PANE_SPAWN_SUBJECT:-$(cd "$(dirname "$0")" && pwd)/pane-spawn}"
SANDBOX="$(mktemp -d "${TMPDIR:-/tmp}/pane-spawn.XXXXXX")"
trap 'rm -rf "$SANDBOX"' EXIT

fail() {
    echo "FAIL: $*" >&2
    [ ! -f "$SANDBOX/err" ] || cat "$SANDBOX/err" >&2
    exit 1
}

# A fake herdr that logs each call to $SANDBOX/log. FAIL_START=<n> fails the first n agent starts, SESSIONLESS=1 keeps
# the session id empty as Codex does until its first prompt, FAIL_WAIT=<n> fails the first n waits for work.
# NOT_READY=1 never reports readiness; FAIL_PROMPT=1 refuses prompt delivery.
# Each line starts with the server the call went to, read from HERDR_SOCKET_PATH: [default] or [workers].
# Use macOS Bash for fixtures called hundreds of times. The real launcher still
# uses the requested Bash, including explicit coverage of both Bash versions.
# Fixed JSON needs no generator process; dynamic strings still go through jq.
cat > "$SANDBOX/herdr" <<EOF
#!/bin/bash
case "\$HERDR_SOCKET_PATH" in */sessions/*) s=\${HERDR_SOCKET_PATH%/herdr.sock}; s=\${s##*/} ;; *) s=default ;; esac
echo "[\$s] \$*" >> "$SANDBOX/log"
[ "\$s" != workers ] || [ -z "\${STUCK_WORKERS:-}" ] || exec /bin/sleep 30 # herdr's CLI waits on a stuck server for ever
case "\$1 \$2" in
  "session list") cat "$SANDBOX/sessions.json" ;;
  "workspace list") cat "$SANDBOX/\$s-workspaces.json" 2>/dev/null || echo '{"result":{"workspaces":[]}}' ;;
  "workspace create") [ -z "\${FAIL_WORKSPACE:-}" ] && printf '%s\n' '{"result":{"workspace":{"workspace_id":"w2"}}}' ;;
  "pane get")
    if [ -z "\${CALLER_LABEL:-}" ]; then
      printf '%s\n' '{"result":{"pane":{"pane_id":"w7:p3","tab_id":"w7:t1","workspace_id":"w7","terminal_id":"term_65f21913323dc21","label":""}}}'
    else
      jq -n --arg label "\$CALLER_LABEL" '{result: {pane: {pane_id: "w7:p3", tab_id: "w7:t1", workspace_id: "w7", terminal_id: "term_65f21913323dc21", label: \$label}}}'
    fi ;; # moved: env says w1:p1
  "agent list") cat "$SANDBOX/\$s-agents.json" 2>/dev/null || echo '{"result":{"agents":[]}}' ;;
  "tab rename") ;;
  "tab create") { [ -z "\${FAIL_WORKERS_TAB:-}" ] || [ "\$s" != workers ]; } && printf '%s\n' '{"result":{"root_pane":{"pane_id":"w1:new"},"tab":{"tab_id":"w1:t9"}}}' ;;
  "pane layout") [ -z "\${FAIL_LAYOUT:-}" ] && cat "$SANDBOX/layout.json" ;;
  "pane split") [ -z "\${FAIL_SPLIT:-}" ] && printf '%s\n' '{"result":{"pane":{"pane_id":"w1:new"}}}' ;;
  "pane rename"|"pane close") ;;
  "agent prompt") touch "$SANDBOX/prompted"; [ -z "\${FAIL_PROMPT:-}" ] ;;
  "agent read") [ -z "\${LATE_DIALOG:-}" ] || [ -e "$SANDBOX/ready-seen" ] || exit 0; printf '%s\n' "\${SCREEN:-}" ;;
  "agent wait")
    n=\$(cat "$SANDBOX/waits" 2>/dev/null || echo 0); echo \$((n + 1)) > "$SANDBOX/waits"
    [ "\$n" -ge "\${FAIL_WAIT:-0}" ] || exit 1
    if [ "\${WAIT_STATUS:-working}" = working ] && [ -z "\${WAIT_PADDING:-}" ]; then
      printf '%s\n' '{"result":{"agent":{"agent_status":"working"}},"padding":""}'
    else jq -n --arg s "\${WAIT_STATUS:-working}" --arg padding "\${WAIT_PADDING:-}" '{result:{agent:{agent_status:\$s}},padding:\$padding}'; fi ;;
  "agent start")
    [[ "\$3" =~ ^[a-z][a-z0-9_-]{0,31}\$ ]] || { echo invalid_agent_name; exit 1; } # herdr's rule
    n=\$(cat "$SANDBOX/starts" 2>/dev/null || echo 0); echo \$((n + 1)) > "$SANDBOX/starts"
    [ -z "\${HANG_START:-}" ] || exec /bin/sleep 30
    [ "\$n" -ge "\${BUSY_START:-0}" ] || { echo '{"error":{"code":"agent_pane_busy"}}'; exit 1; }
    [ -z "\${BLOCKED_START:-}" ] || { echo '{"error":{"code":"agent_not_ready","message":"agent x is blocked during startup and is not ready for prompts"}}'; exit 1; }
    [ -z "\${EXIT_START:-}" ] || { echo '{"error":{"code":"agent_start_failed","message":"agent process exited before becoming interactive"}}'; exit 1; }
    [ -z "\${PARTIAL_START:-}" ] || { echo '{"error":{"code":"agent_wait_timeout"}}'; exit 1; }
    [ "\$n" -ge "\${FAIL_START:-0}" ] ;;
  "agent get")
    [ "\$3" != w1:new ] || touch "$SANDBOX/ready-seen"
    if [ -n "\${NOT_READY:-}" ]; then printf '%s\n' '{"result":{"agent":{"interactive_ready":false}}}'
    elif [ -n "\${SESSIONLESS:-}" ]; then printf '%s\n' '{"result":{"agent":{"interactive_ready":true}}}'
    elif [ -z "\${PARTIAL_START:+registered}\${CALLER_AGENT:-}" ]; then printf '%s\n' '{"result":{"agent":{"name":"","interactive_ready":true,"agent_session":{"value":"sid"}}}}'
    else jq -n --arg n "\${PARTIAL_START:+registered}\${CALLER_AGENT:-}" '{result: {agent: {name: \$n, interactive_ready: true, agent_session: {value: "sid"}}}}'; fi ;;
  *) exit 2 ;;
esac
EOF
chmod +x "$SANDBOX/herdr"
# Keep the fake CLI's process isolation, but avoid starting another Bash for
# each ordinary call from the real launcher. Agent start remains an executable
# so the async launch/kill checks retain their original PID semantics. Perl's
# exec-based timeout paths also run the executable. All use the same fixture.
printf 'function %q { case "$1 $2" in "agent start") exec %q "$@";; *) ( . %q "$@" );; esac; }\n' \
  "$SANDBOX/herdr" "$SANDBOX/herdr" "$SANDBOX/herdr" > "$SANDBOX/bash-env"
export BASH_ENV="$SANDBOX/bash-env"
cat > "$SANDBOX/pane-label" <<EOF
#!/bin/bash
case "\$HERDR_SOCKET_PATH" in */sessions/*) s=\${HERDR_SOCKET_PATH%/herdr.sock}; s=\${s##*/} ;; *) s=default ;; esac
echo "[\$s] pane-label \$*" >> "$SANDBOX/log"
EOF
chmod +x "$SANDBOX/pane-label"

run_on() { local sock=$1; shift; rm -f "$SANDBOX/log" "$SANDBOX/starts" "$SANDBOX/waits" "$SANDBOX/prompted"; env -u HERDR_SOCKET_PATH -u XDG_STATE_HOME ${sock:+HERDR_SOCKET_PATH="$sock"} HOME="$SANDBOX/home" CODEX_HOME="$SANDBOX/home/.codex" CLAUDE_CONFIG_DIR="$SANDBOX/home/.claude" PATH="$SANDBOX:$PATH" HERDR_PANE_ID=w1:p1 HERDR_BIN_PATH="$SANDBOX/herdr" PANE_SPAWN_POLL=0 PANE_SPAWN_START_POLL=0.01 PANE_SPAWN_READY_SETTLE=0 PANE_SPAWN_RETRY_WAIT=0 "${PANE_SPAWN_TEST_SHELL:-bash}" "$SUBJECT" "$@"; }
ps() { run_on "" "$@"; }
log() { cat "$SANDBOX/log" 2>/dev/null || true; }
has() { grep -qF -- "$1" "$SANDBOX/log" || fail "$2: no [$1] in: $(log)"; }
hasnt() { if grep -qF -- "$1" "$SANDBOX/log"; then fail "$2: found [$1] in: $(log)"; fi; }
before() { [ "$(grep -nF -- "$1" "$SANDBOX/log" | head -1 | cut -d: -f1)" -lt "$(grep -nF -- "$2" "$SANDBOX/log" | head -1 | cut -d: -f1)" ] || fail "[$1] should come before [$2]: $(log)"; }

# Exercise the legacy surface under the documented rollback policy; quota-aware cases follow below.
mkdir -p "$SANDBOX/home/.local/state/agent-quota"
echo codex-only > "$SANDBOX/home/.local/state/agent-quota/policy"

# The caller's tab as herdr reports it, one "<pane id> <x> <y> <height>" per pane.
layout() { printf '%s\n' "$@" | jq -Rn '{result: {layout: {panes: [inputs | split(" ") | {pane_id: .[0], rect: {x: (.[1] | tonumber), y: (.[2] | tonumber), height: (.[3] | tonumber)}}]}}}' > "$SANDBOX/layout.json"; }
layout "w7:p3 0 0 46"

# The main pane does no work itself: what it opens goes to the workers server, and its address names that server.
is_main() { mkdir -p "$SANDBOX/home/.local/state/orchestrator"; echo term_65f21913323dc21 > "$SANDBOX/home/.local/state/orchestrator/main"; }
not_main() { rm -f "$SANDBOX/home/.local/state/orchestrator/main"; }
workers_up() { jq -n --arg w "$SANDBOX/sessions/workers/herdr.sock" --argjson r "${1:-true}" '{sessions: [{name: "default", running: true, socket_path: "/x/herdr.sock"}, {name: "workers", running: $r, socket_path: $w}]}' > "$SANDBOX/sessions.json"; }

# Shared expectations also used by the independently discovered check suite.
NOUPDATE="-c check_for_update_on_startup=false"
NPROC="--env PANE_SPAWN_NPROC=5000"
