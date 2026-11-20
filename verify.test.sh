#!/bin/bash
# verify.sh must end with one VERIFY-FAIL line per failed check and a VERIFY-DONE line (read by settings-drift.py).
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
out=$(HOME="$tmp" bash "$DIR/verify.sh" 2>&1)
fail=0
grep -qx 'VERIFY-FAIL .zshrc symlink' <<<"$out" || { echo "FAIL: no VERIFY-FAIL line for a missing symlink"; fail=1; }
[[ $(tail -n 1 <<<"$out") == VERIFY-DONE ]] || { echo "FAIL: last line is not VERIFY-DONE"; fail=1; }
[[ $fail -eq 0 ]] && echo "ok verify"
exit $fail
