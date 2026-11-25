#!/usr/bin/env bash
# AGY launch arguments and fail-closed quota decisions at the herdr boundary.
source "$(cd "$(dirname "$0")" && pwd)/pane-spawn-test-fixture.sh"

agy_fixture() {
  mkdir -p "$SANDBOX/home/.gemini/antigravity-cli"
  printf '{"useG1Credits":false,"modelProvider":""}\n' > "$SANDBOX/home/.gemini/antigravity-cli/settings.json"
  jq -n --argjson now "$(date +%s)" '{captured_at:$now,plan_tier:"Google AI Ultra",quota:{"gemini-5h":{remaining_fraction:0.9,reset_time:($now+1000)},"gemini-weekly":{remaining_fraction:0.8,reset_time:($now+300000)}}}' > "$SANDBOX/home/.local/state/agent-quota/agy.json"
}
no_herdr() { [ ! -s "$SANDBOX/log" ] || fail "$1: $(log)"; }
agy_fixture
for pair in simple:gemini-4-hydrogen-medium normal:gemini-4-neon-high complex:gemini-4-carbon-high; do
  complexity=${pair%%:*} model=${pair#*:}
  ps --kind agy --complexity "$complexity" "AGY $complexity" "Synthetic task" >/dev/null 2>"$SANDBOX/err" || fail "AGY $complexity should open"
  has '--kind agy' 'AGY runtime is selected'
  has "--model $model" "complexity $complexity uses the requested model"
  has '--sandbox' 'AGY keeps the terminal sandbox'
  has 'useG1Credits' 'AGY pins subscription-only settings'
  hasnt '--ask-for-approval' 'Codex options do not enter AGY'
done
CAPTURE_LAUNCH=1 ps --kind agy --task-type research "AGY default" "Synthetic task" >/dev/null 2>"$SANDBOX/err" || fail 'normal complexity is default'
has '--model gemini-4-neon-high' 'default complexity is normal'
has 'HERDR_AGY_TASK_TYPE=research' 'task catalog is independent of complexity'
jq -e '.env as $env | ["GEMINI_API_KEY", "GOOGLE_API_KEY", "AGY_ADC_AUTH", "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_PROJECT_ID", "GOOGLE_CLOUD_QUOTA_PROJECT", "GOOGLE_GENAI_USE_VERTEXAI"] | all(.[]; . as $key | ($env | has($key)) and $env[$key] == "")' "$SANDBOX/launch.json" >/dev/null || fail 'all AGY authentication overrides must be cleared in the pane environment'
jq -e '.args as $args | ($args | index("--settings")) as $i | ($args[$i+1] | fromjson) == {useG1Credits:false,modelProvider:"",allowNonWorkspaceAccess:false,enableTerminalSandbox:true,agentMode:"auto",toolPermission:"proceed-in-sandbox"}' "$SANDBOX/launch.json" >/dev/null || fail 'safe AGY settings must be pinned exactly without replacing statusLine'
for option in '--model alternate' '--settings {}' '--no-sandbox'; do
  if ps --kind agy "AGY unsafe" "Synthetic task" -- $option >/dev/null 2>"$SANDBOX/err"; then fail "AGY forwarded options must be rejected: $option"; fi
  no_herdr 'bad options do not reach herdr'
done
if ps --kind agy --complexity extreme "AGY invalid" "Synthetic task" >/dev/null 2>"$SANDBOX/err"; then fail 'unknown complexity must be rejected'; fi
no_herdr 'bad complexity does not reach herdr'
is_main
if ps --check --kind agy "AGY check" "Synthetic task" >/dev/null 2>"$SANDBOX/err"; then fail 'AGY cannot become a check runtime'; fi
no_herdr 'refused check does not reach herdr'
not_main
rm "$SANDBOX/home/.local/state/agent-quota/agy.json"
if ps --kind agy "AGY stale" "Synthetic task" >/dev/null 2>"$SANDBOX/err"; then fail 'unknown quota must refuse AGY'; fi
no_herdr 'unknown quota does not reach herdr'
agy_fixture
if GEMINI_API_KEY=synthetic ps --kind agy "AGY API" "Synthetic task" >/dev/null 2>"$SANDBOX/err"; then fail 'API override must refuse AGY'; fi
no_herdr 'API override does not reach herdr'
echo agy-only > "$SANDBOX/home/.local/state/agent-quota/policy"
workers_up
is_main
ps --check --kind auto "AGY policy check" "Synthetic task" >/dev/null 2>"$SANDBOX/err" || fail 'AGY policy keeps checks on Claude'
has '--kind claude' 'AGY policy never changes check runtime'
not_main
ps --kind auto "AGY policy" "Synthetic task" >/dev/null 2>"$SANDBOX/err" || fail 'explicit AGY policy chooses AGY'
has '--kind agy' 'auto honors explicit AGY policy'
if ps --kind auto "Pinned Codex policy" "Synthetic task" -- --model alternate >/dev/null 2>"$SANDBOX/err"; then fail 'unsupported options must not select Codex'; fi
no_herdr 'unsupported options under AGY policy do not open a fallback'
rm "$SANDBOX/home/.local/state/agent-quota/agy.json"
if ps --kind auto "AGY unavailable" "Synthetic task" >/dev/null 2>"$SANDBOX/err"; then fail 'AGY policy does not fall back when quota is unknown'; fi
no_herdr 'AGY-only refusal opens no fallback pane'
agy_fixture
SESSIONLESS=1 ps --kind agy "AGY ready" "Synthetic task" >/dev/null 2>"$SANDBOX/err" || fail 'AGY uses interactive readiness before a session id is available'
if FAIL_START=1 ps --kind agy "AGY failed" "Synthetic task" >/dev/null 2>"$SANDBOX/err"; then fail 'uncertain AGY start must not be retried'; fi
[ "$(cat "$SANDBOX/starts")" = 1 ] || fail 'AGY start was sent twice'
hasnt '--kind codex' 'AGY start failure does not fall back to Codex'
mkdir "$SANDBOX/slow-python"
printf '#!/bin/bash\ncase "$2" in */agent-quota) exec /bin/sleep 30;; esac\nexec "%s" "$@"\n' "$(command -v python3)" > "$SANDBOX/slow-python/python3"
chmod +x "$SANDBOX/slow-python/python3"
for runner in agy auto; do
  if PATH="$SANDBOX/slow-python:$PATH" PANE_SPAWN_QUOTA_LIMIT=1 ps --kind "$runner" "AGY slow" "Synthetic task" >/dev/null 2>"$SANDBOX/err"; then fail 'slow AGY admission must refuse'; fi
  no_herdr 'slow AGY admission opens no fallback pane'
  grep -q 'timed out' "$SANDBOX/err" || fail 'slow AGY admission names its blocker'
done
echo 'ok: pane-spawn AGY routing'
