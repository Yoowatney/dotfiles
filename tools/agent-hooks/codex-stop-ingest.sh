#!/usr/bin/env bash
set -uo pipefail

HOOKS_ROOT="${AGENT_HOOKS_ROOT:-$HOME/.dotfiles/tools/agent-hooks}"
payload="$(cat)"

printf '%s' "$payload" \
    | "$HOOKS_ROOT/codex-work-log-guard.sh" finish >/dev/null 2>&1 || true
"$HOOKS_ROOT/run-llm-wiki-ingest.sh" >/dev/null 2>&1 || true

exit 0
