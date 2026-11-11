#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
# Bash 3.2 writes these inputs before launching their reader. Large input can
# fill a 512-byte pipe and deadlock; use a concurrent printf producer instead.
for script in pane-tell pane-spawn codex-work-log-guard.sh; do
  if grep -n '<<' "$ROOT/$script"; then
    echo "FAIL: $script contains a here-document or here-string" >&2
    exit 1
  fi
done
echo 'ok   delivery scripts never prewrite shell input'
