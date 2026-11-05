#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
box=$(mktemp -d)
trap 'rm -rf "$box"' EXIT
mkdir -p "$box/bin" "$box/state/orchestrator"
cat >"$box/bin/dscacheutil" <<'SH'
#!/bin/bash
echo 'ip_address: 127.0.0.1'
SH
cat >"$box/bin/claude" <<'SH'
#!/bin/bash
printf '%s\n' "$@"
SH
cat >"$box/bin/herdr" <<'SH'
#!/bin/bash
echo '{"result":{"pane":{"terminal_id":"term_test"}}}'
SH
chmod +x "$box/bin/"*
run() { env PATH="$box/bin:$PATH" XDG_STATE_HOME="$box/state" HERDR_PANE_ID=w1:p1 CLAUDE_STARTUP_ROLE="${ROLE:-}" CLAUDE_STARTUP_PROFILE="${PROFILE:-}" bash "$ROOT/scripts/claude-online" "$@"; }
out=$(ROLE=worker run 'literal argument $()')
[[ "$out" == --settings$'\n'* ]] || { echo 'FAIL: worker has no startup settings'; exit 1; }
settings=$(sed -n '2p' <<<"$out")
jq -e '.enableWorkflows == false and .enabledPlugins["context7@claude-plugins-official"] == false and (has("hooks") | not) and (has("permissions") | not) and (has("enableArtifact") | not)' <<<"$settings" >/dev/null
[[ "$(tail -1 <<<"$out")" == 'literal argument $()' ]]
out=$(ROLE=main run)
jq -e '.enableArtifact == false and .enableWorkflows == false' <<<"$(sed -n '2p' <<<"$out")" >/dev/null
printf 'term_test\n' >"$box/state/orchestrator/main"
out=$(run)
jq -e '.enableArtifact == false' <<<"$(sed -n '2p' <<<"$out")" >/dev/null
[[ "$(ROLE=worker PROFILE=full run --version)" == --version ]]
rm "$box/state/orchestrator/main"
[[ "$(run --version)" == --version ]]
echo 'PASS: startup role, main detection, opt-out, arguments, and safety settings'
