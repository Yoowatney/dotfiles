#!/usr/bin/env bash
# Keep only the latest N resurrect save files
KEEP=30
RESURRECT_DIR="${HOME}/.local/share/tmux/resurrect"

ls -1t "${RESURRECT_DIR}"/tmux_resurrect_*.txt 2>/dev/null \
  | tail -n +$((KEEP + 1)) \
  | while read -r f; do rm -f "$f"; done
