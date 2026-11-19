#!/bin/bash
# Exercises the real TIS state of this Mac: run it on a Mac set up with init.sh.
set -u
DIR="$(cd "$(dirname "$0")/.." && pwd)"
TOOL="$DIR/scripts/input-sources.sh"
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
fail=0

echo com.apple.keylayout.ABC > "$tmp/want"
out=$(bash "$TOOL" check "$tmp/want"); code=$?
[[ $code -eq 1 ]] || { echo "FAIL: mismatched list should exit 1, got $code"; fail=1; }
grep -qx 'missing com.apple.keylayout.ABC' <<<"$out" || { echo "FAIL: no missing line: $out"; fail=1; }

# A source that is not installed stops apply before it changes anything: a failed 구름 install must not cost the layouts.
before=$(bash "$TOOL" list)
{ cat "$DIR/macos/input-sources/enabled.txt"; echo org.example.not-installed; } > "$tmp/missing"
out=$(bash "$TOOL" apply "$tmp/missing" 2>&1); code=$?
[[ $code -eq 1 ]] || { echo "FAIL: apply with a missing source should exit 1, got $code"; fail=1; }
grep -qx 'not installed org.example.not-installed' <<<"$out" || { echo "FAIL: no not-installed line: $out"; fail=1; }
[[ $(bash "$TOOL" list) == "$before" ]] || { echo "FAIL: apply changed the input sources"; fail=1; }

bash "$TOOL" check >/dev/null; code=$?
[[ $code -eq 0 ]] || { echo "FAIL: enabled.txt should match this Mac, got $code"; fail=1; }

# A failed compile stops the wrapper; running the cached binary would check against the old code.
t="$tmp/tree"; mkdir -p "$t/scripts" "$t/macos/input-sources" "$tmp/home/.cache/dotfiles"
cp "$DIR/scripts/input-sources.sh" "$t/scripts/"
printf '#!/bin/bash\nexit 0\n' > "$tmp/home/.cache/dotfiles/input-sources"
chmod +x "$tmp/home/.cache/dotfiles/input-sources"; touch -t 202001010000 "$tmp/home/.cache/dotfiles/input-sources"
echo 'not swift' > "$t/macos/input-sources/input-sources.swift"; echo x > "$t/macos/input-sources/enabled.txt"
HOME="$tmp/home" bash "$t/scripts/input-sources.sh" check >/dev/null 2>&1; code=$?
[[ $code -ne 0 ]] || { echo "FAIL: a failed compile ran the cached binary"; fail=1; }

[[ $fail -eq 0 ]] && echo "ok input-sources"
exit $fail
