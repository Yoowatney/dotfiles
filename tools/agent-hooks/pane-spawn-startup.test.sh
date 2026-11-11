#!/usr/bin/env bash
# Each pane-spawn*.test.sh entry covers one independent part of the launcher.
# CI and pre-push discover all entries; shared setup contains no cases.
source "$(cd "$(dirname "$0")" && pwd)/pane-spawn-test-fixture.sh"

# Startup screens can look ready to herdr while still consuming the first Enter.
for dialog in 'hook needs review' 'Do you trust the contents of this directory?' 'error: the argument --sandbox cannot be used multiple times'; do
  if SCREEN="$dialog" ps --kind codex "startup" "instruction" >"$SANDBOX/out" 2>"$SANDBOX/err"; then fail "startup blocker must fail: $dialog"; fi
  hasnt "agent prompt" "a startup blocker must never receive the instruction"
  has "pane close w1:new" "an unprompted blocked pane is removed"
done
rm -f "$SANDBOX/ready-seen"
if LATE_DIALOG=1 SCREEN='Do you trust the contents of this directory?' ps --kind codex "late dialog" "instruction" >/dev/null 2>&1; then fail "a dialog appearing after readiness must fail"; fi
hasnt "agent prompt" "recheck the screen after readiness"
if EXIT_START=1 ps --kind codex "exit" "instruction" >/dev/null 2>&1; then fail "immediate process exit must fail"; fi
[ "$(cat "$SANDBOX/starts")" = 1 ] || fail "never restart an exited CLI"
if PARTIAL_START=1 ps --kind codex "partial" "instruction" >/dev/null 2>&1; then fail "partial startup must fail"; fi
[ "$(cat "$SANDBOX/starts")" = 1 ] || fail "never reuse a name after a registered start"
if WAIT_STATUS=blocked ps --kind codex "blocked" "instruction" >/dev/null 2>&1; then fail "a blocker after delivery does not prove work"; fi
hasnt "pane close" "keep an ambiguously delivered instruction instead of terminating work"
BUSY_START=4 ps --kind codex "busy" "instruction" >/dev/null
[ "$(cat "$SANDBOX/starts")" = 5 ] || fail "wait for shell initialization beyond the old three retries"
if HANG_START=1 SCREEN='hook needs review' ps --kind codex "hanging" "instruction" >/dev/null 2>&1; then fail "detect a dialog while agent start is still waiting"; fi
hasnt "agent prompt" "an in-progress startup dialog must not receive a prompt"
SCREEN="$(printf '%20000s' x)" ps --kind codex "large screen" "instruction" >/dev/null
has "agent prompt" "a large non-dialog screen must not stall the launcher"
WAIT_PADDING="$(printf '%20000s' x)" ps --kind codex "large wait response" "instruction" >/dev/null

[ -z "${PANE_SPAWN_STARTUP_ONLY:-}" ] || { echo "pane-spawn startup regressions ok"; exit 0; }

# Codex may report interactive readiness while its hook-review modal owns input.
# Never send an instruction into that modal or silently trust the changed hooks.
for readiness_shell in "$(command -v bash)" /bin/bash; do
 for blocked_start in '' 1; do
  rc=0
  PANE_SPAWN_TEST_SHELL="$readiness_shell" BLOCKED_START="$blocked_start" SESSIONLESS=1 SCREEN='7 hooks need review' ps --kind codex --job-id hook-review-job "hook review" "Run tests" 2>"$SANDBOX/err" >/dev/null || rc=$?
  [ "$rc" = 1 ] || fail "hook review ($readiness_shell): expected failure, got $rc"
  hasnt "agent prompt" "a hook-review modal receives no instruction"
  has "pane close w1:new" "the undelivered test pane is closed"
  hasnt "--kind claude" "hook review does not switch runners"
  grep -q 'hooks need review' "$SANDBOX/err" || fail "reports the hook review blocker"
  hasnt "send-keys" "hook recovery never approves a possibly stale review screen"
  grep -q 'already-open review screen' "$SANDBOX/err" || fail "warns against trusting stale hook inventory"
  grep -q 'After hook installation finishes, open a fresh Codex session' "$SANDBOX/err" || fail "recovery reads current hooks after installation"
  grep -q 'If trust was already registered during installation, do not press t again' "$SANDBOX/err" || fail "installation trust does not require another approval"
  grep -q -- '--job-id hook-review-job' "$SANDBOX/err" || fail "recovery preserves the failed job id"
  if grep -q -- '--kind claude' "$SANDBOX/err"; then fail "hook review recovery must not suggest switching runners"; fi
 done
done

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

# Typed workers receive context metadata, without changing runner or permissions.
SESSIONLESS=1 ps --kind codex --task-type code --job-id test-job "test" "Run tests" >/dev/null
has "--env HERDR_CODEX_STARTUP_ROLE=worker" "only workers opt into the startup profile"
has "--env HERDR_CODEX_TASK_TYPE=code" "the requested task type survives clear"
has "--env HERDR_TASK_ID=test-job" "request and execution share a job id"
mkdir -p "$SANDBOX/home/.codex" "$SANDBOX/home/.agents/skills/hyperframes"
printf 'Safety\n<!-- pane:skip -->\nMain only\n<!-- /pane:skip -->\n' > "$SANDBOX/home/.codex/AGENTS.md"
printf 'demo\n' > "$SANDBOX/home/.agents/skills/hyperframes/SKILL.md"
SESSIONLESS=1 ps --kind codex --task-type code "test" "Run tests" >/dev/null
hasnt "--env CODEX_HOME=" "startup trimming keeps canonical credentials and state"
has '--env HERDR_CODEX_STARTUP_HELPER=' "the runner expands exclusions after entering the shell"
hasnt 'skills.config=[' "large skill flags must not pass through terminal input"
# The installed command is a symlink, often reached through another PATH link.
# Startup metadata and accounting must use helpers beside the resolved script.
real_subject=$(readlink -f "$SUBJECT")
mkdir -p "$SANDBOX/bin" "$SANDBOX/links"
ln -s "$real_subject" "$SANDBOX/links/pane-spawn"
ln -s ../links/pane-spawn "$SANDBOX/bin/pane-spawn"
SUBJECT="$SANDBOX/bin/pane-spawn"
SESSIONLESS=1 ps --kind auto --task-type code --job-id symlink-job "test" "Run tests" >/dev/null
has "--env HERDR_CODEX_STARTUP_HELPER=$(dirname "$real_subject")/codex-startup.py" "installed symlinks retain the startup helper"
python3 - "$SANDBOX/home/.local/state/agent-task-ledger/events.jsonl" <<'PY'
import json, sys
rows = [json.loads(line) for line in open(sys.argv[1])]
assert {'request', 'run'} <= {e['kind'] for e in rows if e['job_id'] == 'symlink-job'}, 'installed symlinks must record request and run together'
PY
SUBJECT=$real_subject
rm -rf "$SANDBOX/home/.codex" "$SANDBOX/home/.agents"
python3 "$(dirname "$SUBJECT")/task-ledger.py" --state-dir "$SANDBOX/home/.local/state/agent-task-ledger" record run --job-id parent-job --session-id parent-session
CODEX_THREAD_ID=parent-session SESSIONLESS=1 ps --kind codex --job-id child-job "test" "Run tests" >/dev/null
python3 - "$SANDBOX/home/.local/state/agent-task-ledger/events.jsonl" <<'PY'
import json, sys
rows = [json.loads(line) for line in open(sys.argv[1])]
assert any(e['kind'] == 'request' and e['job_id'] == 'child-job' and e.get('parent_job_id') == 'parent-job' for e in rows), 'shell environment filtering must not lose the parent job'
PY


echo "ok: pane-spawn startup and profiles"
