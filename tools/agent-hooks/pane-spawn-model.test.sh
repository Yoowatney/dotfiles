#!/usr/bin/env bash
# Each pane-spawn*.test.sh entry covers one independent part of the launcher.
# CI and pre-push discover all entries; shared setup contains no cases.
source "$(cd "$(dirname "$0")" && pwd)/pane-spawn-test-fixture.sh"

workers_up
echo '{"result":{"workspaces":[{"workspace_id":"w1","label":"workers"}]}}' > "$SANDBOX/workers-workspaces.json"
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
