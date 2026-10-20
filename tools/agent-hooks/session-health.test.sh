#!/usr/bin/env bash
set -euo pipefail

SUBJECT="$(cd "$(dirname "$0")" && pwd)/session-health.sh"
SANDBOX="$(cd "$(mktemp -d)" && pwd -P)"
trap 'rm -rf "$SANDBOX"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }
G() { git -c user.email=t@t -c user.name=t "$@"; }

# Bare origin with 30 commits; "far" is reset 25 behind, "near" 3 behind.
mkdir -p "$SANDBOX/ws" "$SANDBOX/home"
G init -q --bare -b main "$SANDBOX/origin.git"
G clone -q "$SANDBOX/origin.git" "$SANDBOX/seed" 2>/dev/null
for i in $(seq 30); do G -C "$SANDBOX/seed" commit -q --allow-empty -m "c$i"; done
G -C "$SANDBOX/seed" push -q origin HEAD:main
for r in far near; do G clone -q "$SANDBOX/origin.git" "$SANDBOX/ws/$r"; done
G -C "$SANDBOX/ws/far" reset -q --hard HEAD~25
G -C "$SANDBOX/ws/near" reset -q --hard HEAD~3

export SESSION_HEALTH_NO_FETCH=1 HOME="$SANDBOX/home" SESSION_HEALTH_ROOT="$SANDBOX/ws"
hook() { echo "{\"cwd\":\"$1\"}" | bash "$SUBJECT" || fail "hook exited non-zero for $1"; }

# Inside a stale repo: warns with the count.
out=$(hook "$SANDBOX/ws/far")
grep -q "far 체크아웃이 origin/main보다 25커밋" <<<"$out" || fail "far should warn: $out"
jq -e '.hookSpecificOutput.hookEventName == "SessionStart"' <<<"$out" >/dev/null || fail "bad JSON shape: $out"

# Below the threshold: silent.
[ -z "$(hook "$SANDBOX/ws/near")" ] || fail "near (3 behind) should be silent"

# Workspace root: ONE combined line, sorted by count; near (3), a -wt- dir and a linked worktree are skipped.
cp -R "$SANDBOX/ws/far" "$SANDBOX/ws/far-wt-x"
G -C "$SANDBOX/ws/far" worktree add -q "$SANDBOX/ws/linked" -b linked HEAD~0 2>/dev/null
for r in a b c d e; do cp -R "$SANDBOX/ws/far" "$SANDBOX/ws/r$r"; done
G -C "$SANDBOX/ws/ra" reset -q --hard HEAD~1 # ra becomes 26 behind
out=$(hook "$SANDBOX/ws")
line=$(jq -r '.hookSpecificOutput.additionalContext' <<<"$out")
[ "$(grep -c . <<<"$line")" = 1 ] || fail "root should be one line: $line"
grep -q "ra 26, far 25, rb 25, rc 25, rd 25 외 1개\. 배포된 동작은" <<<"$line" || fail "bad combined line: $line"
grep -q "near\|wt\|linked" <<<"$line" && fail "near/worktrees should be skipped: $line"
rm -rf "$SANDBOX"/ws/r? "$SANDBOX/ws/far-wt-x"
G -C "$SANDBOX/ws/far" worktree remove --force "$SANDBOX/ws/linked"

# Not a git repo: silent.
[ -z "$(hook "$SANDBOX/home")" ] || fail "non-repo should be silent"

# Memory size.
mdir="$SANDBOX/home/.claude/projects/$(printf '%s' "$SANDBOX/home" | sed 's/[^A-Za-z0-9]/-/g')/memory"
mkdir -p "$mdir"
head -c 19000 /dev/zero | tr '\0' 'a' > "$mdir/MEMORY.md"
[ -z "$(hook "$SANDBOX/home")" ] || fail "19000B memory should be silent"
head -c 23007 /dev/zero | tr '\0' 'a' > "$mdir/MEMORY.md"
out=$(hook "$SANDBOX/home")
grep -q "MEMORY.md가 23,007B로" <<<"$out" || fail "23007B memory should warn: $out"

# Fail open on garbage input and a missing cwd.
echo 'not json' | bash "$SUBJECT" >/dev/null || fail "garbage input should exit 0"
hook "$SANDBOX/nope" >/dev/null

# The checkout is never modified.
[ "$(G -C "$SANDBOX/ws/far" rev-list --count HEAD)" = 5 ] || fail "working tree moved"

echo "ok: session-health"
