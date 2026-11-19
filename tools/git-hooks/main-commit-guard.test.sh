#!/usr/bin/env bash
# The pre-commit and pre-merge-commit hooks refuse a commit or merge commit made while main is checked out:
# main takes changes only through a PR, then a fast-forward pull. Real git in a throwaway repo.
set -uo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
fails=0
check() { if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: expected [$3] got [$2]"; fails=1; fi; }
has() { case "$2" in *"$3"*) echo "ok   $1" ;; *) echo "FAIL $1: [$3] not in [$2]"; fails=1 ;; esac; }

W="$T/w"
git init -q -b main "$W"
git -C "$W" config user.name t
git -C "$W" config user.email t@example.com
mkdir -p "$W/hooks"
cp -P "$DIR/pre-commit" "$DIR/pre-merge-commit" "$W/hooks/"
git -C "$W" config core.hooksPath "$W/hooks"
g() { git -C "$W" "$@" 2>&1 </dev/null; }
put() { printf '%s\n' "$2" > "$W/$1"; }

[ -x "$DIR/pre-commit" ] && [ -e "$DIR/pre-merge-commit" ]; check "both hooks exist and pre-commit is executable" "$?" 0

put a 1; g add a >/dev/null
out=$(g commit -m "on main"); rc=$?
check "a commit on main is refused" "$([ "$rc" != 0 ] && echo refused)" refused
has "the refusal says to use a PR" "$out" "PR"

g commit -q --no-verify -m "root" >/dev/null   # how a fresh clone or a test seeds main
g checkout -q -b feat >/dev/null
put b 2; g add b >/dev/null
out=$(g commit -m "on a branch"); rc=$?
check "a commit on a branch is allowed" "$rc" 0

g checkout -q main >/dev/null
out=$(g merge --ff-only feat); rc=$?
check "a fast-forward of main is allowed" "$rc" 0

# a merge commit on main: main and feat2 diverge
g checkout -q -b feat2 >/dev/null
put c 3; g add c >/dev/null; g commit -q -m "feat2" >/dev/null
g checkout -q main >/dev/null
put d 4; g add d >/dev/null; g commit -q --no-verify -m "main moved" >/dev/null
before=$(git -C "$W" rev-parse HEAD)
out=$(g merge --no-edit feat2); rc=$?
check "a merge commit on main is refused" "$([ "$rc" != 0 ] && echo refused)" refused
check "the refused merge leaves main where it was" "$(git -C "$W" rev-parse HEAD)" "$before"
git -C "$W" merge --abort >/dev/null 2>&1

g checkout -q feat >/dev/null
out=$(g merge --no-edit main); rc=$?
check "a merge commit on a branch is allowed" "$rc" 0

g checkout -q --detach >/dev/null
put e 5; g add e >/dev/null
out=$(g commit -m "detached"); rc=$?
check "a commit on a detached HEAD is allowed" "$rc" 0

exit $fails
