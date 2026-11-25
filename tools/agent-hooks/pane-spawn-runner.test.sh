#!/usr/bin/env bash
# agent-runner: one command shows or sets which agent opens work and check sessions first (policy file under agent-quota).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
box=$(mktemp -d); trap 'rm -rf "$box"' EXIT
export XDG_STATE_HOME="$box/state" HOME="$box/home"
policy="$XDG_STATE_HOME/agent-quota/policy"
fail() { echo "FAIL: $*" >&2; exit 1; }
run() { bash "$here/agent-runner" "$@"; }

out=$(run); grep -q '^runner: agy$' <<<"$out" || fail "no local file shows the installed default: $out"
grep -q 'installed default' <<<"$out" || fail "the source is named: $out"
for pair in claude:claude-first codex:codex-only auto:quota-aware agy:agy-only; do
  name=${pair%%:*} value=${pair##*:}
  out=$(run "$name")
  [ "$(cat "$policy")" = "$value" ] || fail "$name writes $value, got: $(cat "$policy")"
  grep -q "^runner: $name\$" <<<"$out" || fail "$name is reported after setting: $out"
  out=$(run show); grep -q "^runner: $name\$" <<<"$out" || fail "show reports $name: $out"
  grep -q "^policy: $value\$" <<<"$out" || fail "show reports the policy $value: $out"
  grep -q 'local file' <<<"$out" || fail "the local file is named as the source: $out"
done
[ "$(stat -f %Lp "$XDG_STATE_HOME/agent-quota" 2>/dev/null || stat -c %a "$XDG_STATE_HOME/agent-quota")" = 700 ] || fail "state folder is private"
echo typo > "$policy"
out=$(run show) || fail "show survives a bad value"
grep -q '^runner: invalid' <<<"$out" || fail "a bad file value is shown as invalid: $out"
before=$(cat "$policy")
if run purple >/dev/null 2>"$box/err"; then fail "an unknown runner must be refused"; fi
[ "$(cat "$policy")" = "$before" ] || fail "a refused request leaves the file alone"
grep -q 'claude|codex|auto' "$box/err" || fail "usage names the choices"
if run claude codex >/dev/null 2>&1; then fail "extra arguments are refused"; fi
run auto >/dev/null; rm -f "$policy"
out=$(run); grep -q '^runner: agy$' <<<"$out" || fail "removing the file restores the default"
echo "ok: agent-runner"

# AGY-only workers never fall back to Codex, including legacy/pinned callers.
(
  source "$here/pane-spawn-test-fixture.sh"
  echo agy-only > "$SANDBOX/home/.local/state/agent-quota/policy"
  mkdir -p "$SANDBOX/home/.gemini/antigravity-cli"
  printf '{"useG1Credits":false,"modelProvider":""}\n' > "$SANDBOX/home/.gemini/antigravity-cli/settings.json"
  jq -n --argjson now "$(date +%s)" '{captured_at:$now,plan_tier:"Google AI Ultra",quota:{"gemini-5h":{remaining_fraction:0.9,reset_time:($now+1000)},"gemini-weekly":{remaining_fraction:0.8,reset_time:($now+300000)}}}' > "$SANDBOX/home/.local/state/agent-quota/agy.json"
  workers_up
  if ps --kind auto "Pinned worker" "Synthetic task" -- --model codex-fixture >/dev/null 2>"$SANDBOX/err"; then fail 'unsupported forwarded options must be refused'; fi
  [ ! -s "$SANDBOX/log" ] || fail 'unsupported options must not open Codex'
  mkdir -p "$SANDBOX/home/.config/munice"
  echo ambient-runner > "$SANDBOX/home/.config/munice/host-role"
  ps --kind auto "Ambient worker" "Synthetic task" >/dev/null 2>"$SANDBOX/err" || fail 'ambient worker must still open'
  has '--kind agy' 'ambient routing uses AGY'
  rm "$SANDBOX/home/.config/munice/host-role"
  ps "Default worker" "Synthetic task" >/dev/null 2>"$SANDBOX/err" || fail 'default worker must open'
  has '--kind agy' 'default worker uses AGY'
  has '--model gemini-4-hydrogen-xhigh' 'default worker uses the highest Hydrogen effort'
  ps --kind codex "Legacy worker" "Synthetic task" >/dev/null 2>"$SANDBOX/err" || fail 'legacy caller must open AGY'
  has '--kind agy' 'explicit legacy runner does not select Codex'
  is_main
  ps --kind claude --why gcp "GCP worker" "Synthetic task" >/dev/null 2>"$SANDBOX/err" || fail 'legacy GCP caller must open AGY'
  has '--kind agy' 'GCP uses AGY'
  grep -qF -- 'GCP 작업과 재확인은 --kind auto' "$SUBJECT" || fail 'help must name the GCP automatic route'
  grep -qF -- 'pane-spawn --kind auto' "$here/yoyoo-share-guard.py" || fail 'recheck guidance must retain a working GCP route'
)
echo 'ok: default AGY routing regressions'
