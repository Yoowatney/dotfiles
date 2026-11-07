#!/usr/bin/env bash
set -uo pipefail

# Skip model work for marked probes and sessions nested under a worker.
# Resolve the installed symlink before locating the shared helper.
payload="$(cat)"
guard_script="$(python3 -I -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$0")"
guard_root="$(dirname "$guard_script")"
if printf '%s' "$payload" | python3 -I "$guard_root/stop-model-guard.py" --stdin; then
    exit 0
fi

HOOKS_ROOT="${AGENT_HOOKS_ROOT:-$HOME/.dotfiles/tools/agent-hooks}"

printf '%s' "$payload" \
    | "$HOOKS_ROOT/codex-work-log-guard.sh" finish >/dev/null 2>&1 || true
"$HOOKS_ROOT/run-llm-wiki-ingest.sh" >/dev/null 2>&1 || true

exit 0
