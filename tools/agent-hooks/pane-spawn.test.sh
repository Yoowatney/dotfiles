#!/usr/bin/env bash
set -euo pipefail

SUBJECT="$(cd "$(dirname "$0")" && pwd)/pane-spawn"
SANDBOX="$(mktemp -d "${TMPDIR:-/tmp}/pane-spawn.XXXXXX")"
trap 'rm -rf "$SANDBOX"' EXIT

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

# A fake herdr that logs each call to $SANDBOX/log. FAIL_START=<n> fails the first n agent starts, SESSIONLESS=1 keeps
# the session id empty as Codex does until its first prompt, FAIL_WAIT=<n> fails the first n waits for work.
# NOT_READY=1 never reports readiness; FAIL_PROMPT=1 refuses prompt delivery.
# Each line starts with the server the call went to, read from HERDR_SOCKET_PATH: [default] or [workers].
cat > "$SANDBOX/herdr" <<EOF
#!/usr/bin/env bash
case "\$HERDR_SOCKET_PATH" in */sessions/*) s=\${HERDR_SOCKET_PATH%/herdr.sock}; s=\${s##*/} ;; *) s=default ;; esac
echo "[\$s] \$*" >> "$SANDBOX/log"
[ "\$s" != workers ] || [ -z "\${STUCK_WORKERS:-}" ] || exec /bin/sleep 30 # herdr's CLI waits on a stuck server for ever
case "\$1 \$2" in
  "session list") cat "$SANDBOX/sessions.json" ;;
  "workspace list") cat "$SANDBOX/\$s-workspaces.json" 2>/dev/null || echo '{"result":{"workspaces":[]}}' ;;
  "workspace create") [ -z "\${FAIL_WORKSPACE:-}" ] && jq -n '{result: {workspace: {workspace_id: "w2"}}}' ;;
  "pane get") jq -n --arg label "\${CALLER_LABEL:-}" '{result: {pane: {pane_id: "w7:p3", tab_id: "w7:t1", workspace_id: "w7", terminal_id: "term_65f21913323dc21", label: \$label}}}' ;; # moved: env says w1:p1
  "agent list") cat "$SANDBOX/\$s-agents.json" 2>/dev/null || echo '{"result":{"agents":[]}}' ;;
  "tab rename") ;;
  "tab create") { [ -z "\${FAIL_WORKERS_TAB:-}" ] || [ "\$s" != workers ]; } && jq -n '{result: {root_pane: {pane_id: "w1:new"}, tab: {tab_id: "w1:t9"}}}' ;;
  "pane layout") [ -z "\${FAIL_LAYOUT:-}" ] && cat "$SANDBOX/layout.json" ;;
  "pane split") [ -z "\${FAIL_SPLIT:-}" ] && jq -n '{result: {pane: {pane_id: "w1:new"}}}' ;;
  "pane rename"|"pane close") ;;
  "agent prompt") [ -z "\${FAIL_PROMPT:-}" ] ;;
  "agent read") printf '%s\n' "\${SCREEN:-}" ;;
  "agent wait")
    n=\$(cat "$SANDBOX/waits" 2>/dev/null || echo 0); echo \$((n + 1)) > "$SANDBOX/waits"
    [ "\$n" -ge "\${FAIL_WAIT:-0}" ] ;;
  "agent start")
    [[ "\$3" =~ ^[a-z][a-z0-9_-]{0,31}\$ ]] || { echo invalid_agent_name; exit 1; } # herdr's rule
    n=\$(cat "$SANDBOX/starts" 2>/dev/null || echo 0); echo \$((n + 1)) > "$SANDBOX/starts"
    [ -z "\${BLOCKED_START:-}" ] || { echo '{"error":{"code":"agent_not_ready","message":"agent x is blocked during startup and is not ready for prompts"}}'; exit 1; }
    [ "\$n" -ge "\${FAIL_START:-0}" ] ;;
  "agent get")
    if [ -n "\${NOT_READY:-}" ]; then jq -n '{result: {agent: {interactive_ready: false}}}'
    elif [ -n "\${SESSIONLESS:-}" ]; then jq -n '{result: {agent: {interactive_ready: true}}}'
    else jq -n --arg n "\${CALLER_AGENT:-}" '{result: {agent: {name: \$n, interactive_ready: true, agent_session: {value: "sid"}}}}'; fi ;;
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

run_on() { local sock=$1; shift; rm -f "$SANDBOX/log" "$SANDBOX/starts" "$SANDBOX/waits"; env -u HERDR_SOCKET_PATH -u XDG_STATE_HOME ${sock:+HERDR_SOCKET_PATH="$sock"} HOME="$SANDBOX/home" PATH="$SANDBOX:$PATH" HERDR_PANE_ID=w1:p1 HERDR_BIN_PATH="$SANDBOX/herdr" PANE_SPAWN_POLL=0 PANE_SPAWN_RETRY_WAIT=0 bash "$SUBJECT" "$@"; }
ps() { run_on "" "$@"; }
log() { cat "$SANDBOX/log" 2>/dev/null || true; }
has() { grep -qF -- "$1" "$SANDBOX/log" || fail "$2: no [$1] in: $(log)"; }
hasnt() { if grep -qF -- "$1" "$SANDBOX/log"; then fail "$2: found [$1] in: $(log)"; fi; }
before() { [ "$(grep -nF -- "$1" "$SANDBOX/log" | head -1 | cut -d: -f1)" -lt "$(grep -nF -- "$2" "$SANDBOX/log" | head -1 | cut -d: -f1)" ] || fail "[$1] should come before [$2]: $(log)"; }

# The caller's tab as herdr reports it, one "<pane id> <x> <y> <height>" per pane.
layout() { printf '%s\n' "$@" | jq -Rn '{result: {layout: {panes: [inputs | split(" ") | {pane_id: .[0], rect: {x: (.[1] | tonumber), y: (.[2] | tonumber), height: (.[3] | tonumber)}}]}}}' > "$SANDBOX/layout.json"; }
layout "w7:p3 0 0 46"

# Default: the caller's tab, split to its right; the caller is marked 👑, the new pane 🫡 before its agent starts.
out=$(ps --cwd /work "api#1 설계" "할 일입니다")
has "pane-label --lead" "caller becomes the lead"
has "pane split --pane w7:p3 --direction right --cwd /work --no-focus" "a lead alone in its tab keeps the left, the pane goes right"
has "agent start spawn-65f21913323dc21-" "the agent's name records the caller's terminal, within herdr's limit"
hasnt "tab create" "no new tab by default"
has "pane rename w1:new 🟢 🫡 api#1 설계" "the first name carries 🫡"
before "pane rename w1:new" "agent start"
before "pane rename w1:new" "pane-label --sync-tabs"
before "pane-label --sync-tabs" "agent start"
has "--kind claude --pane w1:new" "claude by default"
has "--env CLAUDE_STARTUP_ROLE=worker" "Claude work panes keep their startup profile through clear"
hasnt "CLAUDE_STARTUP_ARTIFACT" "a Claude pane starts without the Artifact tool unless it asks (api#1065)"

has "agent prompt w1:new 할 일입니다" "instruction delivered"
has "요유는 이 칸을 보지 않으니" "a new pane learns its reports go to the lead"
before "agent get w1:new" "agent prompt"
before "agent prompt" "agent wait w1:new --until working --until blocked --until done"
[[ "$out" == *"w1:new"* ]] || fail "prints the new pane: $out"

# api#1065: --artifact keeps the Artifact tool in a Claude pane (the profile turns it off otherwise); Codex has no such tool.
ps --artifact "발행" "지시" >/dev/null
has "--env CLAUDE_STARTUP_ARTIFACT=1" "--artifact keeps the Artifact tool in a Claude pane, through clear"
has "--env CLAUDE_STARTUP_ROLE=worker" "--artifact keeps the rest of the worker profile"
SESSIONLESS=1 ps --artifact --kind codex "조사" "지시" >/dev/null
hasnt "CLAUDE_STARTUP_ARTIFACT" "Codex panes are unaffected by --artifact"
SESSIONLESS=1 ps --artifact --kind auto "조사" "지시" >/dev/null
hasnt "CLAUDE_STARTUP_ARTIFACT" "auto resolves to Codex and is unaffected too"

# api#1066: --skills keeps the design, office-file and Claude-API skills in a Claude pane (a UI or office-file job); the profile cuts them otherwise.
ps "스킬 기본" "지시" >/dev/null
hasnt "CLAUDE_STARTUP_SKILLS" "a Claude pane starts without the unrelated skills unless it asks (api#1066)"
ps --skills "UI 작업" "지시" >/dev/null
has "--env CLAUDE_STARTUP_SKILLS=1" "--skills keeps the cut skills in a Claude pane, through clear"
has "--env CLAUDE_STARTUP_ROLE=worker" "--skills keeps the rest of the worker profile"
SESSIONLESS=1 ps --skills --kind codex "조사" "지시" >/dev/null
hasnt "CLAUDE_STARTUP_SKILLS" "Codex panes are unaffected by --skills"

# 요유 2026-11-01: a job has one lead. A pane under a lead that opens a review pane stays under it, and that pane reports to it.
CALLER_LABEL="🟢 🫡 구현" ps --same-tab "리뷰" "봐 줘" >/dev/null
hasnt "pane-label --lead" "a pane under a lead does not become one"
has "agent start spawn-65f21913323dc21-" "the new pane still reports to the pane that opened it"
CALLER_LABEL="🟢 👑 리드" ps --same-tab "리뷰" "봐 줘" >/dev/null
has "pane-label --lead" "a lead stays one"
CALLER_LABEL="🟢 일" ps "리뷰" "봐 줘" >/dev/null
has "pane-label --lead" "a pane under nobody becomes the lead"

# 요유 2026-11-04: a lead and its panes share one tab, the lead on the whole left, its panes stacked on the right.
layout "w7:p3 0 0 46" "w7:p4 81 0 46"
ps "구현" "지시" >/dev/null
has "pane split --pane w7:p4 --direction down" "the next pane stacks in the right column"
hasnt "tab create" "still no new tab"
layout "w7:p3 0 0 46" "w7:p4 81 0 23" "w7:p5 81 23 11" "w7:p6 81 34 12"
ps "리뷰" "지시" >/dev/null
has "pane split --pane w7:p4 --direction down" "the tallest pane takes it, so heights stay close"
layout "w7:p3 0 0 46" "w7:p4 81 0 23" "w7:p5 81 23 23"
ps "리뷰" "지시" >/dev/null
has "pane split --pane w7:p5 --direction down" "a tie goes to the lowest"
layout "w7:p1 0 0 46" "w7:p3 81 0 46"
CALLER_LABEL="🟢 🫡 구현" ps "리뷰" "지시" >/dev/null
has "pane split --pane w7:p3 --direction down" "a pane under the lead stacks in the column, not beside itself"
layout "w7:p3 0 0 46" "w7:p4 81 0 11" "w7:p5 81 11 11" "w7:p6 81 22 12" "w7:p7 81 34 12"
ps "다섯째" "지시" >/dev/null
has "tab create --workspace w7 --cwd" "a column too short to read: the work gets its own tab"
has "--label 다섯째 --no-focus" "named for the work"
hasnt "pane split" "no pane shorter than can be read"

# A job the lead does not follow asks for its own tab, and a split or layout that fails still opens the pane.
layout "w7:p3 0 0 46"
ps --new-tab --cwd /work "api#1 설계" "지시" >/dev/null
has "tab create --workspace w7 --cwd /work --label api#1 설계 --no-focus" "--new-tab in the caller's current workspace"
hasnt "pane split" "--new-tab splits nothing"
hasnt "pane layout" "--new-tab reads no layout"
FAIL_SPLIT=1 ps "실패" "지시" >/dev/null
has "tab create --workspace w7" "a failed split falls back to a tab"
has "agent prompt w1:new 지시" "and the work still goes out"
FAIL_LAYOUT=1 ps "실패" "지시" >/dev/null
has "tab create --workspace w7" "so does a failed layout read"

# --same-tab was the way to ask for this before it became the default: sessions already running may still pass it.
ps --same-tab "api#1 리뷰" "리뷰" >/dev/null
has "pane split --pane w7:p3 --direction right" "--same-tab still works"
hasnt "tab create" "--same-tab opens no tab"

# Agent arguments pass through, and Codex works without a session id until its first prompt.
SESSIONLESS=1 ps --kind codex "조사" "지시" -- -m gpt-5 >/dev/null
# 2026-11-06: once Codex sees a newer release than it runs, its TUI opens on an update prompt that nobody here answers, so
# every Codex pane failed to start until 0.167.0 was installed (mise installs a release a day after it comes out).
NOUPDATE="-c check_for_update_on_startup=false"
# 요유 2026-11-07: a Codex work pane runs gcloud, bq, network and process commands as a Claude pane does. Its workspace-write
# sandbox let it write only its folder, so gcloud failed as it rewrote its login files and ps saw no other process. Like a
# Claude pane it runs with no sandbox, guarded by the same PreToolUse hooks, and nobody answers an approval prompt.
UNBOXED="--sandbox danger-full-access --ask-for-approval never"
has "--kind codex --pane w1:new --timeout 120000 -- $UNBOXED $NOUPDATE -m gpt-5" "a codex work pane runs unsandboxed, skips its update prompt, then its arguments"
# Codex refuses an option given twice (`cannot be used multiple times`, Codex 0.167), so the caller's own choice wins.
for own in "--sandbox read-only" "--sandbox=read-only" "-s read-only" "-sread-only" "--full-auto" "--yolo" "--dangerously-bypass-approvals-and-sandbox"; do
  # shellcheck disable=SC2086
  SESSIONLESS=1 ps --kind codex "조사" "지시" -- $own >/dev/null
  hasnt "danger-full-access" "the caller's sandbox wins ($own)"
done
for own in "--ask-for-approval on-request" "--ask-for-approval=untrusted" "-a on-request" "-aon-request" "--full-auto" "--yolo" "--dangerously-bypass-approvals-and-sandbox"; do
  # shellcheck disable=SC2086
  SESSIONLESS=1 ps --kind codex "조사" "지시" -- $own >/dev/null
  hasnt "--ask-for-approval never" "the caller's approval policy wins ($own)"
done
SESSIONLESS=1 ps --kind codex "조사" "지시" -- -s read-only >/dev/null
has "--ask-for-approval never -c check_for_update_on_startup=false -s read-only" "an approval the caller left out is still never"
# On the 집맥 (host-role ambient-runner) the global config is the 회사맥's copy, and its guard (host_guard.py) refuses only
# the Write and Edit tools: a Codex shell write or apply_patch there went through once the sandbox was gone (check pane,
# 2026-11-08). So a Codex work pane there keeps workspace-write, with gcloud's folder added and the network on.
mkdir -p "$SANDBOX/home/.config/munice"; echo ambient-runner > "$SANDBOX/home/.config/munice/host-role"
SESSIONLESS=1 ps --kind codex "조사" "지시" >/dev/null
has "-- --sandbox workspace-write --add-dir $SANDBOX/home/.config/gcloud -c sandbox_workspace_write.network_access=true --ask-for-approval never $NOUPDATE" "a 집맥 Codex work pane keeps its sandbox, with gcloud and the network"
hasnt "danger-full-access" "no 집맥 Codex work pane runs unsandboxed"
SESSIONLESS=1 ps --kind codex "조사" "지시" -- -s read-only >/dev/null
hasnt "--add-dir" "the caller's sandbox still wins on the 집맥"
echo other > "$SANDBOX/home/.config/munice/host-role"
SESSIONLESS=1 ps --kind codex "조사" "지시" >/dev/null
has "-- $UNBOXED $NOUPDATE" "another role is no 집맥"
rm "$SANDBOX/home/.config/munice/host-role"
# Only an option counts, not its name inside another argument's value (Codex review).
SESSIONLESS=1 ps --kind codex "조사" "지시" -- -c 'developer_instructions="Explain how --full-auto and -s work."' >/dev/null
has "-- $UNBOXED $NOUPDATE -c developer_instructions=" "an option named inside a value leaves the defaults in"
has "agent prompt w1:new 지시" "codex gets the instruction"
hasnt "CLAUDE_CODE_DISABLE_ADVISOR_TOOL" "a codex pane keeps its environment"

# A Claude pane opened here runs without the advisor tool: the pane's shell gets the variable, so the agent and its subagents do.
ps "조사" "지시" >/dev/null
has "pane split --pane w7:p3 --direction right --cwd $PWD --no-focus --env CLAUDE_CODE_DISABLE_ADVISOR_TOOL=1" "a split pane has no advisor"
hasnt "--model" "an ordinary pane keeps the caller's model"
ps --new-tab "조사" "지시" >/dev/null
has "tab create --workspace w7 --cwd $PWD --label 조사 --no-focus --env CLAUDE_CODE_DISABLE_ADVISOR_TOOL=1" "a pane in a new tab has none either"

# 2026-11-05: a runaway in one pane filled the user's process limit (8000) and froze every pane and launchd job. Every pane
# opened here, Claude or Codex, starts its shell with a lower limit, which the agent and all it runs inherit.
NPROC="--env PANE_SPAWN_NPROC=5000"
has "--label 조사 --no-focus --env CLAUDE_CODE_DISABLE_ADVISOR_TOOL=1 --env CLAUDE_STARTUP_ROLE=worker $NPROC" "a Claude pane in a new tab: the lower limit"
ps "조사" "지시" >/dev/null
has "--direction right --cwd $PWD --no-focus --env CLAUDE_CODE_DISABLE_ADVISOR_TOOL=1 --env CLAUDE_STARTUP_ROLE=worker $NPROC" "a split Claude pane: the lower limit"
SESSIONLESS=1 ps --kind codex "조사" "지시" >/dev/null
has "pane split --pane w7:p3 --direction right --cwd $PWD --no-focus --env MISE_" "a Codex pane"
has "$NPROC" "a Codex pane: the lower limit"
# The pane's shell is what lowers it: zshrc reads the variable. Checked on a real zsh with this repo's zshrc.
mkdir -p "$SANDBOX/zdot" && ln -s "$(cd "$(dirname "$SUBJECT")/../../shell/zsh" && pwd)/zshrc" "$SANDBOX/zdot/.zshrc"
got=$(PANE_SPAWN_NPROC=4321 ZDOTDIR="$SANDBOX/zdot" zsh -i -c 'ulimit -Su' 2>/dev/null </dev/null | tail -1)
[ "$got" = 4321 ] || fail "a pane's shell lowers its process limit to PANE_SPAWN_NPROC: got [$got]"

# A pane that was not ready for the first start is tried again.
FAIL_START=1 ps "재시도" "지시" >/dev/null
[ "$(cat "$SANDBOX/starts")" = 2 ] || fail "should retry a failed start"
has "agent prompt w1:new 지시" "retry delivers"

# A start that keeps failing removes the empty pane and says so.
if FAIL_START=9 ps "실패" "지시" 2>/dev/null; then fail "a failed start must exit non-zero"; fi
has "pane close w1:new" "the empty pane goes"
hasnt "agent prompt" "nothing to prompt"

# An agent held by a dialog as it starts keeps the name it was given, so a retry could only fail on that name and hide why.
# It is given up at once, quoting what the dialog says: on 2026-11-06 the message guessed a trust question, and the dialog
# was Codex's update prompt.
if BLOCKED_START=1 SCREEN=$'  Update available · 0.166.0 → 0.167.0\n\n› 1. Update now\n  2. Skip' ps --kind codex "막힘" "지시" 2>"$SANDBOX/err"; then fail "a start held by a dialog must exit non-zero"; fi
[ "$(cat "$SANDBOX/starts")" = 1 ] || fail "no retry after a dialog: $(cat "$SANDBOX/starts") starts"
has "pane close w1:new" "the held pane goes"
before "agent read w1:new" "pane close w1:new"
grep -qF "Update available · 0.166.0 → 0.167.0 › 1. Update now 2. Skip" "$SANDBOX/err" || fail "quotes the dialog on one line: $(cat "$SANDBOX/err")"
grep -q "trust" "$SANDBOX/err" || fail "still says how to answer a trust dialog: $(cat "$SANDBOX/err")"
grep -q -- "--kind claude" "$SANDBOX/err" || fail "names the way around it: $(cat "$SANDBOX/err")"

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

# Main-pane Claude work defaults must not leak into other callers, checks or agent kinds.
# Assert the actual agent-start arguments, not the instruction or launcher source.
model_cases=0
model_case() { # <model or empty> <effort or empty> <pane-spawn arguments...>
  local model=$1 effort=$2 start
  shift 2
  ps "$@" > /dev/null 2>"$SANDBOX/model-err" || fail "model case failed to launch: $*: $(cat "$SANDBOX/model-err")"
  start=$(grep ' agent start ' "$SANDBOX/log")
  [[ "$start" != *" --opus"* ]] || fail "the launcher must consume --opus: $start"
  if [ -n "$model" ]; then
    [[ "$start" == *"--model $model"* ]] || fail "$*: expected model $model: $start"
  else
    [[ "$start" != *--model* ]] || fail "$*: expected no model override: $start"
  fi
  if [ -n "$effort" ]; then
    [[ "$start" == *"--effort $effort"* ]] || fail "$*: expected effort $effort: $start"
  else
    [[ "$start" != *--effort* ]] || fail "$*: expected no effort override: $start"
  fi
  model_cases=$((model_cases + 1))
}
is_main
model_case claude-sonnet-5-5 high --why skill "Model defaults" "Run the task"
grep -q 'Sonnet.*--opus' "$SANDBOX/model-err" || fail "the trial reports Sonnet and its opt-out on stderr"
[ "$(wc -l < "$SANDBOX/model-err" | tr -d ' ')" = 1 ] || fail "the model notice is one line"
model_case claude-sonnet-5-5 high --kind claude --why skill "Model defaults" "Run the task"
model_case '' '' --opus --why skill "Model defaults" "Run the task"
grep -q -- '--opus' "$SANDBOX/model-err" || fail "the opt-out reports the global default on stderr"
model_case '' '' --kind claude --why skill --opus "Model defaults" "Run the task"
model_case opus xhigh --why skill "Model defaults" "Run the task" -- --model opus --effort xhigh
# The same caller-supplied overrides supported by Claude checks remain supported for work panes.
ps --why skill "Model defaults" "Run the task" -- --model=opus --effort=xhigh >/dev/null 2>"$SANDBOX/model-err"
has "--model=opus --effort=xhigh" "explicit equals-form overrides pass through"
hasnt "claude-sonnet-5-5" "no second model after an explicit override"
hasnt "--effort high" "no second effort after an explicit override"
# api#1065: the main pane's Claude work and check panes start without the Artifact tool, and --artifact brings it back.
ps --why skill "Artifact off" "Run the task" >/dev/null 2>&1
hasnt "CLAUDE_STARTUP_ARTIFACT" "a main-pane work pane starts without the Artifact tool"
ps --check --kind claude "Artifact off" "Run the check" >/dev/null 2>&1
hasnt "CLAUDE_STARTUP_ARTIFACT" "a Claude check pane starts without the Artifact tool"
ps --check --kind claude --artifact "Artifact on" "Run the check" >/dev/null 2>&1
has "--env CLAUDE_STARTUP_ARTIFACT=1" "a Claude check pane can opt back in"
ps --why skill --artifact "Artifact on" "Publish the page" >/dev/null 2>&1
has "--env CLAUDE_STARTUP_ARTIFACT=1" "a main-pane work pane can opt back in"
# api#1066: the main pane's Claude work and check panes start without the unrelated skills, and --skills brings them back.
ps --why skill "Skills off" "Run the task" >/dev/null 2>&1
hasnt "CLAUDE_STARTUP_SKILLS" "a main-pane work pane starts without the unrelated skills"
ps --check --kind claude "Skills off" "Run the check" >/dev/null 2>&1
hasnt "CLAUDE_STARTUP_SKILLS" "a Claude check pane starts without the unrelated skills"
ps --check --kind claude --skills "Skills on" "Run the check" >/dev/null 2>&1
has "--env CLAUDE_STARTUP_SKILLS=1" "a Claude check pane can opt back in"
ps --why skill --skills "Skills on" "Build the page" >/dev/null 2>&1
has "--env CLAUDE_STARTUP_SKILLS=1" "a main-pane work pane can opt back in"
model_cases=$((model_cases + 1))
model_case claude-sonnet-5-5 high --check --kind claude "Model defaults" "Run the task"
model_case claude-sonnet-5-5 high --opus --check --kind claude "Model defaults" "Run the task"
model_case '' '' --check "Model defaults" "Run the task"
model_case '' '' --kind codex "Model defaults" "Run the task"
model_case '' '' --kind codex --opus "Model defaults" "Run the task"
model_case '' '' --kind auto "Model defaults" "Run the task"
MUNICE_AUTO_DISPATCH=1 model_case '' '' --why skill "Model defaults" "Run the task"
MUNICE_AMBIENT=1 model_case '' '' --why skill "Model defaults" "Run the task"
not_main
CALLER_LABEL="🟢 👑 Lead" model_case '' '' "Model defaults" "Run the task"
CALLER_LABEL="🟢 🫡 Worker" model_case '' '' "Model defaults" "Run the task"
echo "ok: pane-spawn model defaults ($model_cases cases)"

is_main; out=$(ps --why skill "api#1 설계" "할 일")
has "[workers] tab create --workspace w1 " "a pane the main pane opens goes to workers"
hasnt "[default] tab create" "nothing opens beside the main pane"
hasnt "pane split" "the main pane keeps its tab to itself"
has "[default] pane get w1:p1" "the caller is read on its own server"
has "[default] pane-label --lead" "the caller is marked on its own server"
has "[workers] pane-label --sync-tabs" "tab marks are synced where the new tab is"
has "[workers] agent start spawn-65f21913323dc21-" "the name still records the main pane's terminal"
has "[workers] agent prompt w1:new" "the instruction reaches the workers pane"
has "보고 첫 줄에" "a pane under the main pane learns its first line is its summary"
[ "$out" = "workers/w1:new" ] || fail "prints an address with the server: $out"

rm "$SANDBOX/workers-workspaces.json"; ps --why skill "api#2" "할 일" >/dev/null
has "[workers] workspace create --label workers --no-focus" "an empty workers server gets its workspace"
before "workspace create" "tab create --workspace w2"

# 요유 2026-11-05: a tab named `자동 칸을 workers로 이전` read as a dispatch tab, so pane-tell refused its pane. A work name never
# starts a job tab's name; the pane keeps the work name as given.
for word in 자동 알아서; do
  ps --why skill "$word 칸을 workers로 이전" "할 일" >/dev/null
  has "[workers] tab create --workspace w2 --cwd $PWD --label 일 $word 칸을 workers로 이전 --no-focus" "the main pane's tab: no job tab's first word ($word)"
  hasnt "--label $word " "no tab starts with $word"
  has "pane rename w1:new 🟢 🫡 $word 칸을 workers로 이전" "the pane keeps the work name"
done
ps --why skill "자동화 정리" "할 일" >/dev/null
has "--label 자동화 정리 --no-focus" "자동화 is no job tab's first word"
not_main; echo '{"result":{"workspaces":[{"workspace_id":"w1","label":"workers"}]}}' > "$SANDBOX/workers-workspaces.json"
CALLER_LABEL="🟢 자동 칸 정리" ps "구현" "할 일" >/dev/null
has "[workers] tab rename w7:t1 일 자동 칸 정리" "a lead's tab named for it: no job tab's first word"
hasnt "--label 자동 " "and none when the tab opens"
FAIL_SPLIT=1 ps --new-tab "자동 칸 고치기" "할 일" >/dev/null
hasnt "--label 자동 " "a --new-tab name goes the same way"
rm -f "$SANDBOX/workers-workspaces.json"; is_main
ps --why skill "api#2-2" "할 일" >/dev/null

workers_up false; if ps --why skill "api#3" "할 일" 2>"$SANDBOX/err"; then fail "workers down must fail"; fi
grep -q "com.yoyoo.herdr-workers" "$SANDBOX/err" || fail "says how to start it: $(cat "$SANDBOX/err")"
hasnt "tab create" "no pane opens anywhere when workers is down"
workers_up

if ps --same-tab --why skill "리뷰" "봐 줘" 2>/dev/null; then fail "the main pane never splits its own tab"; fi
hasnt "pane split" "no split beside the main pane"

if FAIL_START=9 ps --why skill "실패" "지시" 2>/dev/null; then fail "a failed start must exit non-zero"; fi
has "[workers] pane close w1:new" "a failed start removes the workers pane"

hasnt "확인 칸입니다" "a pane that does the job is no check pane"

# 요유 2026-11-05 (api#1002): a fresh pane checks a pane's report for the main pane. Its agent name says so; a rename cannot change that.
out=$(ps --check "api#1 로그인" "npm test를 돌려 보세요")
has "[workers] tab create --workspace " "a check pane opens with the other workers"
has "[workers] pane rename w1:new 🟢 🫡 확인 api#1 로그인" "its name says it is a check pane"
has "[workers] agent start spawn-65f21913323dc21-chk" "its agent name tells auto-tab-close, and still records who opened it"
has "npm test를 돌려 보세요" "the instruction arrives"
has "확인 칸입니다" "it hears what a check pane does"
has "고치지 말고" "a check pane changes nothing"
has "다른 칸의 보고를 다시 읽는 것은 확인이 아닙니다" "rereading a report is no check"
has "보고 첫 줄에" "its first line is what the main pane reads"
[ "$out" = "workers/w1:new" ] || fail "a check pane's address names its server: $out"
# 2026-11-05: a check pane runs on Codex's spare weekly allowance, in Codex's sandbox, so a write to the repo fails instead of
# relying on the instruction. Nobody answers its approval prompts, so a blocked command fails at once.
# 요유 2026-11-07: a check pane reads GCP, GitHub, the board and the network itself, so the network is on, and gcloud's own
# folder is the one more place it writes: gcloud rewrites its login files on every call. The checked folder stays read-only.
SANDBOXED="--sandbox workspace-write --ask-for-approval never -c sandbox_workspace_write.network_access=true"
has "--kind codex --pane w1:new --timeout 120000 -- $SANDBOXED" "a check pane is a sandboxed Codex with the network"
has "-c sandbox_workspace_write.writable_roots=[\"$SANDBOX/home/.config/gcloud\"]" "gcloud's folder is the one writable root it adds"
hasnt "network_access=false" "the network is not shut"
hasnt "danger-full-access" "a check pane keeps its sandbox"
has "Codex 샌드박스" "it hears what the sandbox blocks"
hasnt "--model" "a codex check pane keeps its model"
hasnt "--effort" "and its effort"
hasnt "CLAUDE_CODE_DISABLE_ADVISOR_TOOL" "and its environment"
# It may write only in a fixed empty folder outside any repo, besides temp: a test runs, and the repo stays as it was. Codex asks
# whether to trust a repo it does not know after it reports ready, and the instruction pane-spawn types then answers yes and
# is lost (2026-11-05, ~/.dotfiles). That folder is no repo, so nothing asks; the repo goes in the instruction instead.
CHECK_DIR="$SANDBOX/home/.local/state/pane-spawn/check"
has "tab create --workspace w2 --cwd $CHECK_DIR --label 확인 api#1 로그인" "it starts in the fixed folder"
[ -d "$CHECK_DIR" ] || fail "the fixed folder is made"
has "$PWD" "the folder to check, by absolute path, in the instruction"
has "$PWD/AGENTS.md" "Codex reads no rules of a folder it did not start in, so it is told where they are"
# MCP servers and plugins run outside the sandbox, so each one that can write is off: every server the user's Codex config
# names, a new one included, except two that keep only their read tools. Codex's own connectors (apps) and plugins are off.
RULES="$CHECK_DIR/.codex/rules/check.rules"
has "-c projects={\"$CHECK_DIR\"={trust_level=\"trusted\"}}" "the fixed folder is trusted, so Codex loads its rules and asks nothing"
has "--disable apps --disable plugins" "connectors and plugins are off"
# Codex's TUI serves its own MCP tools (create_worktree, in a trusted repo, runs git outside the sandbox) unless a configured
# server holds their name; then it starts without them. Neither `--disable worktrees` (a removed feature, still on) nor
# `-c mcp_servers.codex_worktrees.enabled=false` (no command: Codex does not start) does it (2026-11-05, Codex 0.166).
has '-c mcp_servers.codex_worktrees={command="/usr/bin/false",enabled=false}' "Codex's worktree tools are off"
has '-c mcp_servers.codex_tui={command="/usr/bin/false",enabled=false}' "and its task tools, served under this name with a daemon"
hasnt "mcp_servers.slack" "a config with no MCP server turns none of its own off"
hasnt "worktrees.enabled" "no server that Codex cannot start"
mkdir -p "$SANDBOX/home/.codex"
printf '%s\n' '[mcp_servers.slack]' 'command = "npx"' '[mcp_servers."notion-token"]' 'command = "x"' \
  '[mcp_servers.munice]' 'url = "https://x"' '[mcp_servers.brand_new]' 'command = "x"' > "$SANDBOX/home/.codex/config.toml"
ps --check "로그인" "돌려 보세요" >/dev/null
# Codex splits a -c path at each dot and keeps quotes in the name: `mcp_servers."x".enabled=false` made a new server with no
# command, and Codex refused to start (2026-11-05). So names go bare.
has '-c mcp_servers.notion-token.enabled=false' "a server that can write is off"
has '-c mcp_servers.brand_new.enabled=false' "so is one added later"
has '-c mcp_servers.slack.enabled_tools=["slack_list_channels", "slack_get_channel_history", "slack_get_thread_replies", "slack_get_users", "slack_get_user_profile"]' "Slack keeps only its read tools"
hasnt "slack_post_message" "no Slack post"
hasnt "slack_reply_to_thread" "no Slack reply"
hasnt "slack_add_reaction" "no Slack reaction"
has '-c mcp_servers.munice.enabled_tools=["ask_code", "ask_data"]' "munice keeps only its questions"
# Under --ask-for-approval never, a tool Codex would ask about is refused: the Slack reads failed so (2026-11-05).
has '-c mcp_servers.slack.default_tools_approval_mode="approve"' "Slack's read tools run without asking"
has '-c mcp_servers.munice.default_tools_approval_mode="approve"' "and munice's"
hasnt 'notion-token.default_tools_approval_mode' "a server that is off gets no approval"
hasnt 'mcp_servers."' "no quoted server name"
has "slack_get_thread_replies" "the instruction names the Slack read tools"
hasnt "gh-read" "with the network on, gh reads GitHub itself"
for bad in 'not toml [' '[mcp_servers."a.b"]'; do
  echo "$bad" > "$SANDBOX/home/.codex/config.toml"
  if ps --check "로그인" "돌려 보세요" 2>"$SANDBOX/err" >/dev/null; then fail "a server it cannot turn off must stop a Codex check pane ($bad)"; fi
  grep -q -- "--kind claude" "$SANDBOX/err" || fail "and say what to do: $(cat "$SANDBOX/err")"
  hasnt "agent start" "no check pane opens with a server it could not turn off ($bad)"
done
rm -rf "$SANDBOX/home/.codex"
# Inside the sandbox ps sees no other process and launchctl lists nothing (2026-11-07), so rules let ps, pgrep and launchctl's
# two read verbs run outside it, each only from its real path: Codex resolves an absolute path by its last part, so without
# host_executable a ps the agent wrote in temp would run outside too. ego-browser, which the user's own rules let out, drives a
# browser that may be signed in to Slack, so here it is forbidden. The gh verbs that write a pull request or an issue are
# forbidden too: with the network on, only the instruction stopped them.
[ -f "$RULES" ] || fail "the check pane's rules are written"
ls -A "$CHECK_DIR/.codex/rules" | grep -qv '^check.rules$' && fail "no temp file is left beside the rules: $(ls -A "$CHECK_DIR/.codex/rules")"
if command -v codex >/dev/null 2>&1; then
  mkdir -p "$SANDBOX/execpolicy-home" "$SANDBOX/execpolicy-tmp"
  decide() { TMPDIR="$SANDBOX/execpolicy-tmp" CODEX_HOME="$SANDBOX/execpolicy-home" codex execpolicy check --resolve-host-executables -r "$RULES" -r "$SANDBOX/user.rules" -- "$@" | jq -r '.decision // "sandbox"'; }
  echo 'prefix_rule(pattern=["ego-browser"], decision="allow")' > "$SANDBOX/user.rules" # as in ~/.codex/rules/default.rules
  for c in "allow ps -A" "allow /bin/ps -A" "allow pgrep -fl codex" "allow launchctl list" "allow launchctl print gui/501" \
    "sandbox /tmp/x/ps -A" "sandbox /tmp/x/pgrep x" "sandbox /tmp/x/launchctl list" "sandbox launchctl kickstart gui/501/x" \
    "forbidden gh pr merge 3" "forbidden gh pr comment 3 -b x" "forbidden gh issue comment 3 -b x" "forbidden gh issue close 3" \
    "sandbox gh pr view 3" "sandbox gh issue view 3" \
    "forbidden ego-browser open x" "forbidden /Users/x/.local/bin/ego-browser open x" "sandbox curl -d x https://slack.com/api/chat.postMessage"; do
    want=${c%% *} cmd=${c#* }
    # shellcheck disable=SC2086
    [ "$(decide $cmd)" = "$want" ] || fail "rules: $cmd should be $want, is $(decide $cmd)"
  done
fi
ps --cwd /work/repo --check "로그인" "돌려 보세요" >/dev/null
has "확인 칸입니다" "options in any order"
has "--cwd $CHECK_DIR " "a --cwd the caller gives is checked from the fixed folder too"
has "/work/repo" "and named in the instruction"
ps --check "로그인" "돌려 보세요" -- -m gpt-5 >/dev/null
grep -qE -- "-- $SANDBOXED .*--disable plugins $NOUPDATE -m gpt-5\$" "$SANDBOX/log" || fail "the caller's Codex arguments follow the sandbox, and a check pane skips the update prompt: $(log)"
ps --check --kind codex "로그인" "돌려 보세요" >/dev/null
has "--kind codex --pane w1:new --timeout 120000 -- $SANDBOXED" "--kind codex is the same"
SESSIONLESS=1 ps --kind codex --cwd /work/repo "조사" "지시" >/dev/null
has "--cwd /work/repo " "a Codex pane that does the job starts where it is told"
# 2026-11-05: Codex runs commands with mise's shims first on PATH. A test that swaps HOME then hid mise's config and installs from
# it: python3 re-ran its own shim for ever, or met mise's trust error. Every Codex pane gets mise's folders by absolute path.
# Values already set win, so this suite run inside a Codex pane (which has them, and needs them to reach python3) expects those.
H=$SANDBOX/home
MISE_ENV="--env MISE_CONFIG_DIR=${MISE_CONFIG_DIR:-${XDG_CONFIG_HOME:-$H/.config}/mise} --env MISE_DATA_DIR=${MISE_DATA_DIR:-${XDG_DATA_HOME:-$H/.local/share}/mise} --env MISE_STATE_DIR=${MISE_STATE_DIR:-$H/.local/state/mise} --env MISE_CACHE_DIR=${MISE_CACHE_DIR:-$H/Library/Caches/mise}"
has "--no-focus $MISE_ENV" "a Codex pane keeps mise's folders when a test swaps HOME"
ps --check "로그인" "돌려 보세요" >/dev/null
has "--no-focus $MISE_ENV" "so does a Codex check pane"
has "[workers] tab create --workspace w2 --cwd $CHECK_DIR --label 확인 로그인 --no-focus $MISE_ENV $NPROC" "and a workers pane, the lower limit"
ps --why skill "조사" "지시" >/dev/null
hasnt "MISE_" "a Claude pane runs commands without the shims first, so it needs none"
ps --why skill "조사" "지시" >/dev/null
has "--kind claude" "a pane that does the job is still Claude"
hasnt "--sandbox" "and has no sandbox"
# A check that must write to GitHub, Slack or the board asks for Claude: Sonnet at high effort, as before.
out=$(ps --check --kind claude "api#1 로그인" "npm test를 돌려 보세요")
has "[workers] tab create --workspace w2 --cwd $PWD --label 확인 api#1 로그인 --no-focus --env CLAUDE_CODE_DISABLE_ADVISOR_TOOL=1" "a workers pane has no advisor"
has "--kind claude --pane w1:new --timeout 120000 -- --model claude-sonnet-5-5 --effort high" "a Claude check pane runs Sonnet at high effort"
hasnt "--sandbox" "a Claude check pane gets no Codex flag"
hasnt "Codex 샌드박스" "nor the sandbox note"
has "--cwd $PWD " "and starts where it is told"
has "확인 칸입니다" "it is still a check pane"
ps --check --kind claude "로그인" "돌려 보세요" -- --model opus >/dev/null
has "-- --effort high --model opus" "the caller's model wins"
hasnt "claude-sonnet-5-5" "and no second model"
ps --check --kind claude "로그인" "돌려 보세요" -- --model=opus --effort=xhigh >/dev/null
hasnt "claude-sonnet-5-5" "the caller's --model= wins"
hasnt "--effort high" "so does the caller's --effort="

# api#1051: recorded usage never selects Claude; it cannot prove a current quota block after a coupon reset.
usage() { # <used percent> <seconds until reset> [secondary]: a Codex session record as Codex writes it
  mkdir -p "$SANDBOX/home/.codex/sessions/2026/11/05"
  local week; week=$(jq -n --argjson u "$1" --argjson r "$(($(date +%s) + $2))" '{used_percent: $u, window_minutes: 10080, resets_at: $r}')
  local five='{"used_percent": 99, "window_minutes": 300, "resets_at": 1}'
  if [ -n "${3:-}" ]; then local p=$five s=$week; else local p=$week s=null; fi
  { echo '{"type":"session_meta","payload":{}}'
    jq -nc --argjson p "$p" --argjson s "$s" '{type: "event_msg", payload: {type: "token_count", rate_limits: {limit_id: "codex", primary: $p, secondary: $s}}}'; } > "$SANDBOX/home/.codex/sessions/2026/11/05/rollout-a.jsonl"
}
for percent in 0 69 70 71 99 100; do
  for window in primary secondary; do
    usage "$percent" 3600 "$([ "$window" != secondary ] || echo secondary)"
    ps --check "로그인" "돌려 보세요" 2>"$SANDBOX/err" >/dev/null
    has "--kind codex" "$percent ($window): a Codex check pane"
    has "--sandbox workspace-write --ask-for-approval never" "$percent ($window): check restrictions remain"
    hasnt "--kind claude" "$percent ($window): no automatic Claude fallback"
    SESSIONLESS=1 ps --kind auto "구현" "지시" 2>"$SANDBOX/err" >/dev/null
    has "--kind codex" "$percent ($window): an auto work pane runs Codex"
    hasnt "--sandbox workspace-write" "a work pane is no check pane"
    has "--sandbox danger-full-access" "$percent ($window): an auto work pane runs unsandboxed"
    hasnt "확인 칸입니다" "nor told it is one"
  done
done
usage 100 -60
ps --check "로그인" "돌려 보세요" >/dev/null
has "--kind codex" "a reset week still runs Codex"
usage 100 3600; echo '{"type":"event_msg","payload":{"type":"token_count","rate_limits":null}}' >> "$SANDBOX/home/.codex/sessions/2026/11/05/rollout-a.jsonl"
mkdir -p "$SANDBOX/home/.codex/sessions/2026/11/06"; echo '{"type":"session_meta","payload":{}}' > "$SANDBOX/home/.codex/sessions/2026/11/06/rollout-b.jsonl"
touch -t "$(date -v-1H +%Y%m%d%H%M)" "$SANDBOX/home/.codex/sessions/2026/11/05/rollout-a.jsonl"
ps --check "로그인" "돌려 보세요" >/dev/null
has "--kind codex" "stale recorded exhaustion does not prove a current block"
echo 'not JSON' > "$SANDBOX/home/.codex/sessions/2026/11/06/rollout-b.jsonl"
SESSIONLESS=1 ps --kind auto "구현" "지시" >/dev/null
has "--kind codex" "unreadable usage does not select Claude"
rm -rf "$SANDBOX/home/.codex/sessions"
ps --check "로그인" "돌려 보세요" >/dev/null
has "--kind codex" "missing usage does not select Claude"

# Startup failures are not proof of exhausted quota. Do not silently start another agent or send the job twice.
rc=0
FAIL_START=9 ps --kind auto "실패" "지시" 2>"$SANDBOX/err" >/dev/null || rc=$?
[ "$rc" = 1 ] || fail "auto startup failure: expected exit 1, got $rc"
has "--kind codex" "auto startup failure stays Codex"
hasnt "--kind claude" "no fallback on an unclassified start error"
has "pane close w1:new" "a failed start removes its empty pane"
hasnt "agent prompt" "a failed start delivers no job"
grep -q "codex did not start" "$SANDBOX/err" || fail "reports the startup error"
rc=0
BLOCKED_START=1 SCREEN='You have hit your usage limit' ps --check "막힘" "지시" 2>"$SANDBOX/err" >/dev/null || rc=$?
[ "$rc" = 1 ] || fail "an unstructured dialog must fail, got $rc"
hasnt "--kind claude" "dialog text alone cannot prove a current quota block"
grep -qF 'You have hit your usage limit' "$SANDBOX/err" || fail "reports the actual dialog"
rc=0
NOT_READY=1 ps --kind auto "준비 실패" "지시" 2>"$SANDBOX/err" >/dev/null || rc=$?
[ "$rc" = 1 ] || fail "readiness timeout: expected exit 1, got $rc"
hasnt "--kind claude" "readiness timeout does not select Claude"
has "pane close w1:new" "a pane that never became ready is removed"
hasnt "agent prompt" "an unready pane gets no job"
grep -q 'codex never became ready' "$SANDBOX/err" || fail "reports readiness timeout"
rc=0
FAIL_PROMPT=1 ps --kind auto "전달 실패" "지시" 2>"$SANDBOX/err" >/dev/null || rc=$?
[ "$rc" = 1 ] || fail "prompt failure: expected exit 1, got $rc"
hasnt "--kind claude" "prompt failure does not select Claude"
has "pane close w1:new" "a rejected delivery removes its pane"
hasnt "agent wait" "rejected delivery does not wait for work"
grep -q 'the instruction was not delivered' "$SANDBOX/err" || fail "reports prompt failure"
rc=0
FAIL_WAIT=1 ps --kind auto "무응답" "지시" 2>"$SANDBOX/err" >/dev/null || rc=$?
[ "$rc" = 1 ] || fail "auto unseen work: expected exit 1, got $rc"
hasnt "--kind claude" "ambiguous delivery never starts a duplicate Claude job"
hasnt "pane close" "an accepted job may still be running"
[ "$(grep -c 'agent prompt w1:new 지시' "$SANDBOX/log")" = 1 ] || fail "auto delivers only once"
usage 71 3600
ps --check --kind codex "로그인" "돌려 보세요" >/dev/null
has "--kind codex" "a kind the caller names wins"
ps --why skill "조사" "지시" >/dev/null
has "--kind claude" "an ordinary pane never reads the usage"
# api#1036: the main pane opens a Claude work pane only for a reason Codex cannot cover. Slack posts belong to the main pane,
# so a Slack reason is refused. Since 요유 2026-11-07 a Codex work pane runs GCP commands too, so a GCP reason is refused as
# well. Check panes and other callers stay as they were.
refused() { # <case> [pane-spawn options]
  local why=$1; shift
  if ps "$@" "구현" "지시" 2>"$SANDBOX/err" >/dev/null; then fail "$why: the main pane's Claude pane needs a reason"; fi
  grep -qF -- "--kind auto" "$SANDBOX/err" || fail "$why: points to --kind auto: $(cat "$SANDBOX/err")"
  grep -qF -- "--why" "$SANDBOX/err" || fail "$why: names --why: $(cat "$SANDBOX/err")"
  hasnt "tab create" "$why: no pane opens"
  hasnt "pane-label --lead" "$why: nothing is marked"
}
usage 50 3600
refused "no kind, no reason"
refused "claude, no reason" --kind claude
refused "an empty reason" --kind claude --why ""
refused "a blank reason" --why "  "
ps --kind claude --why skill "UI 작업" "지시" >/dev/null; has "--kind claude" "the main pane accepts a reason Codex cannot cover"
for reason in gcp GCP "gcloud로 배포 상태 확인" "bq 조회" "BigQuery 집계"; do
  for kind_args in default claude; do
    opts=(); [ "$kind_args" != claude ] || opts=(--kind claude)
    rc=0
    ps "${opts[@]}" --why "$reason" "GCP 확인" "지시" >"$SANDBOX/out" 2>"$SANDBOX/err" || rc=$?
    [ "$rc" = 2 ] || fail "the main pane rejects --why [$reason] ($kind_args): expected exit 2, got $rc"
    grep -qF -- "GCP 명령도 Codex 칸이 합니다" "$SANDBOX/err" || fail "GCP refusal says Codex runs it: $(cat "$SANDBOX/err")"
    grep -qF -- "--kind auto" "$SANDBOX/err" || fail "GCP refusal points to --kind auto: $(cat "$SANDBOX/err")"
    hasnt "agent start" "a GCP reason starts no agent"
  done
done
ps --check --kind claude --why gcp "로그인" "돌려 보세요" >/dev/null; has "--kind claude" "a check pane skips the GCP reason check"
# Slack posts belong to the main pane; mentioning Slack cannot justify a Claude work pane there.
for reason in slack "Slack에 답글" "슬랙에 답글" "SLACK에 답글"; do
  for kind_args in default claude; do
    opts=(); [ "$kind_args" != claude ] || opts=(--kind claude)
    rc=0
    ps "${opts[@]}" --why "$reason" "Slack 답글" "지시" >"$SANDBOX/out" 2>"$SANDBOX/err" || rc=$?
    [ "$rc" = 2 ] || fail "the main pane rejects --why [$reason] ($kind_args): expected exit 2, got $rc"
    grep -qF -- "Slack 글은 메인 칸이 올립니다: 밑 칸은 결과를 보고하고 --kind auto로 여세요" "$SANDBOX/err" || fail "Slack refusal explains who posts: $(cat "$SANDBOX/err")"
    hasnt "tab create" "a Slack reason opens no pane"
    hasnt "pane-label --lead" "a Slack reason marks nothing"
    hasnt "agent start" "a Slack reason starts no agent"
  done
done
ps --why "Claude 전용 스킬 필요" "배포 확인" "지시" >/dev/null; has "--kind claude" "a reason opens Claude: the default kind"
ps --why "작업 폴더 밖 파일 수정" "설정 수정" "지시" >/dev/null; has "--kind claude" "another reason still opens Claude"
SESSIONLESS=1 ps --kind codex "구현" "지시" >/dev/null; has "--kind codex" "Codex needs no reason"
SESSIONLESS=1 ps --kind auto "구현" "지시" >/dev/null; has "--kind codex" "auto needs no reason"
usage 71 3600
ps --kind auto "구현" "지시" 2>/dev/null >/dev/null; has "--kind codex" "auto at high usage still needs no reason"
ps --check --kind claude "로그인" "돌려 보세요" >/dev/null; has "--kind claude" "a check pane needs no reason"
ps --check --kind claude --why "Slack에 답글" "로그인" "돌려 보세요" >/dev/null; has "--kind claude" "a check pane skips the Slack reason check"
not_main
CALLER_LABEL="🟢 👑 api#1 로그인" ps "설계" "할 일" >/dev/null; has "--kind claude" "a lead other than the main pane needs no reason"
CALLER_LABEL="🟢 👑 api#1 로그인" ps --why slack "설계" "할 일" >/dev/null; has "--kind claude" "another lead's Slack reason is unchanged"
CALLER_LABEL="🟢 🫡 구현" ps "리뷰" "지시" >/dev/null; has "--kind claude" "nor does a pane under a lead"
is_main
rm -rf "$SANDBOX/home/.codex"

# Every other lead checks results itself, as does a pane under it, and a main-pane file that is stale or gone is no main pane.
for state in other stale missing; do
  case "$state" in
    other) not_main ;;
    stale) mkdir -p "$SANDBOX/home/.local/state/orchestrator"; echo term_gone > "$SANDBOX/home/.local/state/orchestrator/main" ;;
    missing) not_main ;;
  esac
  if ps --check "로그인" "돌려 보세요" 2>"$SANDBOX/err"; then fail "--check outside the main pane must fail ($state)"; fi
  grep -q "직접 확인" "$SANDBOX/err" || fail "says what to do instead ($state): $(cat "$SANDBOX/err")"
  hasnt "tab create" "no pane opens ($state)"
  hasnt "pane split" "no pane opens ($state)"
  hasnt "agent start" "no agent starts ($state)"
done
is_main

# 요유 2026-11-05: an ordinary lead's tab holds only the lead. Its panes run on workers, one tab per lead there.
not_main; LEAD="🟢 👑 api#1 로그인"
echo '{"result":{"workspaces":[{"workspace_id":"w1","label":"workers"}]}}' > "$SANDBOX/workers-workspaces.json"
out=$(CALLER_LABEL=$LEAD ps --cwd /work "설계" "할 일")
has "[default] pane-label --lead" "the caller is marked on its own server"
has "[workers] tab create --workspace w1 --cwd /work --label api#1 로그인 --no-focus" "the tab says whose panes these are"
has "[workers] pane rename w1:new 🟢 🫡 설계" "the pane says what it does"
has "[workers] pane-label --sync-tabs" "marks are synced where the tab is"
has "[workers] agent start spawn-65f21913323dc21-" "the name records the lead's terminal"
hasnt "[default] pane split" "nothing opens beside the lead"
hasnt "[default] tab create" "nothing opens on the lead's server"
hasnt "보고 첫 줄에" "only panes under the main pane get the summary line"
hasnt "확인 칸입니다" "no check pane anywhere else"
[ "$out" = "workers/w1:new" ] || fail "an address that names the server: $out"

# Design, build and review of one job share a tab: the lead's newest pane on workers takes the next one. --same-tab is the same.
jq -n '{result: {agents: [{pane_id: "w1:q2", name: "spawn-65f21913323dc21-7"}, {pane_id: "w1:q3", name: "spawn-65f21913323dc21-8"}, {pane_id: "w1:q9", name: "spawn-OTHER-1"}]}}' > "$SANDBOX/workers-agents.json"
layout "w1:q3 0 0 41"
out=$(CALLER_LABEL=$LEAD ps "구현" "지시")
has "[workers] pane layout --pane w1:q3" "the lead's newest pane, not another lead's"
has "[workers] pane split --pane w1:q3 --direction right" "the same layout as ever"
has "[workers] tab rename w7:t1 api#1 로그인" "a lead that moved on renames its tab"
before "tab rename" "pane-label --sync-tabs"
hasnt "tab create" "no second tab"
[ "$out" = "workers/w1:new" ] || fail "an address that names the server: $out"
CALLER_LABEL=$LEAD ps --same-tab "리뷰" "봐 줘" >/dev/null
has "[workers] pane split" "--same-tab joins the lead's tab on workers"
hasnt "tab create" "--same-tab opens no tab"
layout "w1:q2 0 0 41" "w1:q3 52 0 10" "w1:q4 52 10 10" "w1:q5 52 20 10" "w1:q6 52 30 11"
CALLER_LABEL=$LEAD ps "여섯째" "지시" >/dev/null
has "[workers] tab create --workspace w1 --cwd" "a full tab: another tab of the same lead"
has "--label api#1 로그인 --no-focus" "named for the lead as well"
layout "w1:q3 0 0 41"
CALLER_LABEL=$LEAD ps --new-tab "별개 일" "지시" >/dev/null
has "[workers] tab create --workspace w1" "--new-tab: its own tab on workers"
hasnt "pane split" "--new-tab splits nothing"
hasnt "[default] tab create" "--new-tab stays hidden too"
jq -n '{result: {agents: [{pane_id: "w1:q9", name: "spawn-OTHER-1"}]}}' > "$SANDBOX/workers-agents.json"
CALLER_LABEL="🔴 👑 다른 일" ps "조사" "지시" >/dev/null
has "--label 다른 일 --no-focus" "several leads at once: each its own tab"
hasnt "pane split" "another lead's pane takes nothing"
CALLER_LABEL="" ps "이름 없는 리드" "지시" >/dev/null
has "--label 이름 없는 리드 --no-focus" "a lead with no name yet: the work names the tab"
CALLER_LABEL="🟢 새 세션 munice" ps "이름 없는 리드" "지시" >/dev/null
has "--label 이름 없는 리드 --no-focus" "the name a new session starts with is no name"
rm "$SANDBOX/workers-agents.json"

# 요유 2026-11-05: what a Home lead's pane says may reach the main pane once that lead closes, and it stays off company Slack and the
# board. The agent name says so, since a rename cannot change it and the lead's workspace cannot be read after it closes.
hasnt "agent start spawn-65f21913323dc21-h" "a pane of any other lead carries no mark"
echo '{"result":{"workspaces":[{"workspace_id":"w7","label":"Home"}]}}' > "$SANDBOX/default-workspaces.json"
CALLER_LABEL="🟢 👑 집 일" ps "조사" "지시" >/dev/null
has "[workers] agent start spawn-65f21913323dc21-h" "a Home lead's pane is marked"
is_main; ps --why skill "메인 일" "지시" >/dev/null; hasnt "agent start spawn-65f21913323dc21-h" "the main pane is no Home lead, wherever it sits"; not_main
rm "$SANDBOX/default-workspaces.json"
CALLER_LABEL="🟢 🫡 구현" CALLER_AGENT="spawn-OTHER-h77" ps "조사" "지시" >/dev/null
has "[default] agent start spawn-65f21913323dc21-h" "a pane under a Home lead passes the mark on"
CALLER_LABEL="🟢 🫡 구현" CALLER_AGENT="spawn-OTHER-77" ps "조사" "지시" >/dev/null
hasnt "agent start spawn-65f21913323dc21-h" "any other pane has none to pass on"

# Who stays where it is: a pane under a lead, a job tab (pane-tell refuses a job pane, so a hidden child could reach nobody),
# and any server but default.
layout "w7:p3 0 0 46"
stays() { has "pane split --pane w7:p3" "$1"; hasnt "[workers] tab create" "$1"; hasnt "[workers] agent start" "$1"; [ "$2" = "w1:new" ] || fail "$1: a bare id: $2"; }
out=$(CALLER_LABEL="🟢 🫡 구현" ps "리뷰" "지시"); stays "a pane under a lead" "$out"
out=$(CALLER_LABEL=$LEAD MUNICE_AUTO_DISPATCH=1 ps "리뷰" "지시"); stays "a dispatch tab" "$out"
out=$(CALLER_LABEL=$LEAD MUNICE_AMBIENT=1 ps "리뷰" "지시"); stays "an ambient tab" "$out"
out=$(CALLER_LABEL=$LEAD run_on "$SANDBOX/sessions/resume/herdr.sock" "리뷰" "지시"); stays "another named server" "$out"
has "[resume] pane split" "it opens on its own server"
out=$(CALLER_LABEL=$LEAD run_on "$SANDBOX/sessions/workers/herdr.sock" "리뷰" "지시"); [ "$out" = "w1:new" ] || fail "from workers, a bare id: $out"
has "[workers] pane split --pane w7:p3" "a lead on workers opens beside itself"

# workers cannot take it: an ordinary lead opens in its own tab as before and says so, with no workers socket left behind.
falls_back() { # <why> <pane-spawn's stdout>
  has "[default] pane split --pane w7:p3" "$1: its own tab"
  has "[default] pane rename w1:new" "$1: on its own server"
  has "[default] pane-label --sync-tabs" "$1: on its own server"
  has "[default] agent start" "$1: on its own server"
  [ "$2" = "w1:new" ] || fail "$1: a bare id: $2"
  grep -q "workers 서버" "$SANDBOX/err" || fail "$1: says so: $(cat "$SANDBOX/err")"
}
workers_up false; out=$(CALLER_LABEL=$LEAD ps "리뷰" "지시" 2>"$SANDBOX/err") || fail "workers down: a lead still gets its pane"; falls_back "workers down" "$out"
jq -n '{sessions: [{name: "default", running: true, socket_path: "/x/herdr.sock"}]}' > "$SANDBOX/sessions.json"
out=$(CALLER_LABEL=$LEAD ps "리뷰" "지시" 2>"$SANDBOX/err") || fail "no workers server: a lead still gets its pane"; falls_back "no workers server (집맥)" "$out"
rm "$SANDBOX/sessions.json"
out=$(CALLER_LABEL=$LEAD ps "리뷰" "지시" 2>"$SANDBOX/err") || fail "no session list: a lead still gets its pane"; falls_back "no session list" "$out"
workers_up
out=$(CALLER_LABEL=$LEAD FAIL_WORKERS_TAB=1 ps "리뷰" "지시" 2>"$SANDBOX/err") || fail "a workers tab that fails: a lead still gets its pane"; falls_back "a workers tab that fails" "$out"
mv "$SANDBOX/workers-workspaces.json" "$SANDBOX/kept"
out=$(CALLER_LABEL=$LEAD FAIL_WORKSPACE=1 ps "리뷰" "지시" 2>"$SANDBOX/err") || fail "no workspace there: a lead still gets its pane"; falls_back "no workspace on workers" "$out"
mv "$SANDBOX/kept" "$SANDBOX/workers-workspaces.json"
out=$(CALLER_LABEL=$LEAD ps "리뷰" "지시" 2>"$SANDBOX/err"); [ ! -s "$SANDBOX/err" ] || fail "a pane on workers says nothing: $(cat "$SANDBOX/err")"
# A stuck server still says it runs. The lead falls back in seconds, and a server that may only be slow gets no second workspace.
started=$SECONDS
out=$(CALLER_LABEL=$LEAD STUCK_WORKERS=1 PANE_SPAWN_WORKERS_LIMIT=1 ps "리뷰" "지시" 2>"$SANDBOX/err") || fail "a stuck workers: a lead still gets its pane"; falls_back "a stuck workers" "$out"
[ $((SECONDS - started)) -lt 10 ] || fail "a stuck workers held the lead for $((SECONDS - started)) s"
hasnt "[workers] workspace create" "nothing is created after a list that timed out"
# The main pane never falls back: it does no work, and neither does its tab.
is_main
if STUCK_WORKERS=1 PANE_SPAWN_WORKERS_LIMIT=1 ps --why skill "메인 일" "지시" 2>"$SANDBOX/err"; then fail "the main pane fails on a stuck workers"; fi
if FAIL_WORKERS_TAB=1 ps --why skill "메인 일" "지시" 2>"$SANDBOX/err"; then fail "the main pane fails when workers opens nothing"; fi
hasnt "[default] pane split" "nothing beside the main pane"
hasnt "[default] tab create" "nothing beside the main pane"
grep -q "com.yoyoo.herdr-workers" "$SANDBOX/err" || fail "says how to start it: $(cat "$SANDBOX/err")"
ps --why skill "메인 일" "지시" >/dev/null
has "--label 메인 일 --no-focus" "the main pane's tabs keep the work's name"
hasnt "tab rename" "and nothing renames them"
hasnt "agent list" "the main pane's panes share no tab, so it looks for none"
not_main

# api#1049: workers-move opens the work of a 회사맥 pane on 집맥 over ssh, with no calling pane. --opener names the main pane
# whose pane it was; the agent name gets `r`, which auto-tab-close and workers-watch read on 집맥.
remote() { rm -f "$SANDBOX/log" "$SANDBOX/starts" "$SANDBOX/waits"; env -u HERDR_SOCKET_PATH -u XDG_STATE_HOME -u HERDR_PANE_ID HOME="$SANDBOX/home" PATH="$SANDBOX:$PATH" HERDR_BIN_PATH="$SANDBOX/herdr" PANE_SPAWN_POLL=0 PANE_SPAWN_RETRY_WAIT=0 bash "$SUBJECT" "$@"; }
workers_up
out=$(remote --opener term_abc123 --cwd /work "api#1 이어 감" "메모를 읽고 이어 가세요") || fail "--opener needs no calling pane"
[ "$out" = "workers/w1:new" ] || fail "--opener prints a workers address: $out"
has "[workers] tab create --workspace w1 --cwd /work --label api#1 이어 감 --no-focus" "each moved pane gets its own workers tab, named for the work"
has "[workers] agent start spawn-abc123-r" "the agent name records the main pane and marks a moved pane"
has "보고 첫 줄에 이 칸에서 한 일을 한 문장으로" "a moved pane reports in the main pane's short form"
hasnt "pane get" "there is no calling pane to look up"
hasnt "pane-label --lead" "nobody becomes a lead"
hasnt "[default] tab create" "nothing opens on the default server"
workers_up false
if remote --opener term_abc123 --cwd /work "api#1 이어 감" "지시" 2>"$SANDBOX/err"; then fail "--opener fails with workers down"; fi
hasnt "tab create" "no pane anywhere with workers down"
hasnt "pane split" "no pane anywhere with workers down"
workers_up
is_main; remote --opener term_65f21913323dc21 "이어 감" "지시" >/dev/null || fail "--opener on a Mac whose main pane has that terminal"
has "agent start spawn-65f21913323dc21-r" "--opener is never the main pane itself, whose --why rule would refuse it"; not_main
if remote --opener term_abc123 --check "x" "y" 2>/dev/null; then fail "--opener with --check is bad usage"; fi
if remote --opener "" "x" "y" 2>/dev/null; then fail "an empty --opener is bad usage"; fi

echo "ok: pane-spawn"
