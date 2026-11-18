#!/usr/bin/env bash
# agent-runner: one command shows or sets which agent opens work and check sessions first (policy file under agent-quota).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
box=$(mktemp -d); trap 'rm -rf "$box"' EXIT
export XDG_STATE_HOME="$box/state" HOME="$box/home"
policy="$XDG_STATE_HOME/agent-quota/policy"
fail() { echo "FAIL: $*" >&2; exit 1; }
run() { bash "$here/agent-runner" "$@"; }

out=$(run); grep -q '^runner: auto$' <<<"$out" || fail "no local file shows the installed default: $out"
grep -q 'installed default' <<<"$out" || fail "the source is named: $out"
for pair in claude:claude-first codex:codex-only auto:quota-aware; do
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
out=$(run); grep -q '^runner: auto$' <<<"$out" || fail "removing the file restores the default"
echo "ok: agent-runner"
