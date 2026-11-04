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
# the session id empty as Codex does until its first prompt, FAIL_WAIT=<n> fails the first n waits for work.
# Each line starts with the server the call went to, read from HERDR_SOCKET_PATH: [default] or [workers].
cat > "$SANDBOX/herdr" <<EOF
#!/usr/bin/env bash
case "\$HERDR_SOCKET_PATH" in */sessions/*) s=\${HERDR_SOCKET_PATH%/herdr.sock}; s=\${s##*/} ;; *) s=default ;; esac
echo "[\$s] \$*" >> "$SANDBOX/log"
case "\$1 \$2" in
  "session list") cat "$SANDBOX/sessions.json" ;;
  "workspace list") cat "$SANDBOX/\$s-workspaces.json" 2>/dev/null || echo '{"result":{"workspaces":[]}}' ;;
  "workspace create") jq -n '{result: {workspace: {workspace_id: "w2"}}}' ;;
  "pane get") jq -n --arg label "\${CALLER_LABEL:-}" '{result: {pane: {pane_id: "w7:p3", workspace_id: "w7", terminal_id: "term_65f21913323dc21", label: \$label}}}' ;; # moved: env says w1:p1
  "tab create") jq -n '{result: {root_pane: {pane_id: "w1:new"}, tab: {tab_id: "w1:t9"}}}' ;;
  "pane split") jq -n '{result: {pane: {pane_id: "w1:new"}}}' ;;
  "pane rename"|"agent prompt"|"pane close") ;;
  "agent wait")
    n=\$(cat "$SANDBOX/waits" 2>/dev/null || echo 0); echo \$((n + 1)) > "$SANDBOX/waits"
    [ "\$n" -ge "\${FAIL_WAIT:-0}" ] ;;
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
cat > "$SANDBOX/pane-label" <<EOF
#!/usr/bin/env bash
case "\$HERDR_SOCKET_PATH" in */sessions/*) s=\${HERDR_SOCKET_PATH%/herdr.sock}; s=\${s##*/} ;; *) s=default ;; esac
echo "[\$s] pane-label \$*" >> "$SANDBOX/log"
EOF
chmod +x "$SANDBOX/pane-label"

ps() { rm -f "$SANDBOX/log" "$SANDBOX/starts" "$SANDBOX/waits"; env -u HERDR_SOCKET_PATH -u XDG_STATE_HOME HOME="$SANDBOX/home" PATH="$SANDBOX:$PATH" HERDR_PANE_ID=w1:p1 HERDR_BIN_PATH="$SANDBOX/herdr" PANE_SPAWN_POLL=0 PANE_SPAWN_RETRY_WAIT=0 bash "$SUBJECT" "$@"; }
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
has "요유는 이 칸을 보지 않으니" "a new pane learns its reports go to the lead"
before "agent get w1:new" "agent prompt"
before "agent prompt" "agent wait w1:new --until working --until blocked --until done"
[[ "$out" == *"w1:new"* ]] || fail "prints the new pane: $out"

# 요유 2026-11-01: a job has one lead. A pane under a lead that opens a review pane stays under it, and that pane reports to it.
CALLER_LABEL="🟢 🫡 구현" ps --same-tab "리뷰" "봐 줘" >/dev/null
hasnt "pane-label --lead" "a pane under a lead does not become one"
has "agent start spawn-65f21913323dc21-" "the new pane still reports to the pane that opened it"
CALLER_LABEL="🟢 👑 리드" ps --same-tab "리뷰" "봐 줘" >/dev/null
has "pane-label --lead" "a lead stays one"
CALLER_LABEL="🟢 일" ps "리뷰" "봐 줘" >/dev/null
has "pane-label --lead" "a pane under nobody becomes the lead"

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

# Work that never shows fails loudly, with no resend and the pane left open: an instruction Codex dropped looks the same as a
# wait that failed on an accepted one, and resending that would run the job twice.
if out=$(FAIL_WAIT=1 ps "무응답" "지시" 2>"$SANDBOX/err"); then fail "no work seen must exit non-zero"; fi
[ "$(grep -c "agent prompt w1:new 지시" "$SANDBOX/log")" = 1 ] || fail "sends once: $(log)"
hasnt "pane close" "the pane stays"
[[ "$out" == *"w1:new"* ]] || fail "still names the pane: $out"
grep -q "pane-tell" "$SANDBOX/err" || fail "says what to do: $(cat "$SANDBOX/err")"

# Bad usage.
if ps "이름만" 2>/dev/null; then fail "missing instruction must exit non-zero"; fi
if ps --kind gemini "이름" "지시" 2>/dev/null; then fail "unknown kind must exit non-zero"; fi
if env -u HERDR_PANE_ID HERDR_BIN_PATH="$SANDBOX/herdr" bash "$SUBJECT" "이름" "지시" 2>/dev/null; then fail "outside herdr must exit non-zero"; fi

# The main pane does no work itself: what it opens goes to the workers server, and its address names that server.
is_main() { mkdir -p "$SANDBOX/home/.local/state/orchestrator"; echo term_65f21913323dc21 > "$SANDBOX/home/.local/state/orchestrator/main"; }
not_main() { rm -f "$SANDBOX/home/.local/state/orchestrator/main"; }
workers_up() { jq -n --arg w "$SANDBOX/sessions/workers/herdr.sock" --argjson r "${1:-true}" '{sessions: [{name: "default", running: true, socket_path: "/x/herdr.sock"}, {name: "workers", running: $r, socket_path: $w}]}' > "$SANDBOX/sessions.json"; }
workers_up; echo '{"result":{"workspaces":[{"workspace_id":"w1","label":"workers"}]}}' > "$SANDBOX/workers-workspaces.json"

is_main; out=$(ps "api#1 설계" "할 일")
has "[workers] tab create --workspace w1 " "a pane the main pane opens goes to workers"
hasnt "[default] tab create" "nothing opens beside the main pane"
has "[default] pane get w1:p1" "the caller is read on its own server"
has "[default] pane-label --lead" "the caller is marked on its own server"
has "[workers] pane-label --sync-tabs" "tab marks are synced where the new tab is"
has "[workers] agent start spawn-65f21913323dc21-" "the name still records the main pane's terminal"
has "[workers] agent prompt w1:new" "the instruction reaches the workers pane"
has "보고 첫 줄에" "a pane under the main pane learns its first line is its summary"
[ "$out" = "workers/w1:new" ] || fail "prints an address with the server: $out"

rm "$SANDBOX/workers-workspaces.json"; ps "api#2" "할 일" >/dev/null
has "[workers] workspace create --label workers --no-focus" "an empty workers server gets its workspace"
before "workspace create" "tab create --workspace w2"

workers_up false; if ps "api#3" "할 일" 2>"$SANDBOX/err"; then fail "workers down must fail"; fi
grep -q "com.yoyoo.herdr-workers" "$SANDBOX/err" || fail "says how to start it: $(cat "$SANDBOX/err")"
hasnt "tab create" "no pane opens anywhere when workers is down"
workers_up

if ps --same-tab "리뷰" "봐 줘" 2>/dev/null; then fail "the main pane never splits its own tab"; fi
hasnt "pane split" "no split beside the main pane"

if FAIL_START=9 ps "실패" "지시" 2>/dev/null; then fail "a failed start must exit non-zero"; fi
has "[workers] pane close w1:new" "a failed start removes the workers pane"

not_main; out=$(ps "api#4" "할 일")
has "[default] tab create --workspace w7" "any other caller opens on its own server as before"
hasnt "보고 첫 줄에" "only panes under the main pane get the summary line"
[ "$out" = "w1:new" ] || fail "a bare id as before: $out"

echo "ok: pane-spawn"
