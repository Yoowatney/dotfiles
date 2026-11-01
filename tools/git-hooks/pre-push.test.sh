#!/usr/bin/env bash
# Real pushes to a bare remote through the real hook. The sandbox repo holds only the small tests below, so this
# test does not run itself when the hook runs it.
set -uo pipefail

HOOK="$(cd "$(dirname "$0")" && pwd)/pre-push"
INIT_LINE=$(grep 'config core.hooksPath' "$(dirname "$0")/../../init.sh")
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
fails=0
check() { if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: expected [$3] got [$2]"; fails=1; fi; }
has() { case "$2" in *"$3"*) echo "ok   $1" ;; *) echo "FAIL $1: [$3] not in [$2]"; fails=1 ;; esac; }

W="$T/w" R="$T/r.git"
fresh() {
  rm -rf "$W" "$R" "$T/ran" "$T/old"
  git init -q --bare "$R"
  git init -q -b main "$W"
  git -C "$W" config user.name t
  git -C "$W" config user.email t@example.com
  git -C "$W" remote add origin "$R"
  mkdir -p "$W/tools/git-hooks"
  cp "$HOOK" "$W/tools/git-hooks/pre-push"
  DOTFILES="$W" eval "$INIT_LINE"   # the line a new clone gets
}
put() { printf '%s\n' "$2" > "$W/$1"; }
ci() { git -C "$W" add -A && git -C "$W" commit -q -m "$1"; }
head_of() { git -C "$1" rev-parse -q --verify "${2:-main}" 2>/dev/null; }
push() { out=$(git -C "$W" push "$@" 2>&1 </dev/null); rc=$?; }
GOOD=':'
BAD='echo boom; exit 1'

[ -x "$HOOK" ]; check "the hook is executable: git skips one that is not" "$?" 0

fresh
put tools/ok.test.sh "$GOOD"; ci good
push origin main
check "passing tests: pushed" "$rc|$(head_of "$R")" "0|$(head_of "$W")"

before=$(head_of "$R")
put tools/bad.test.sh "$BAD"; ci bad
push origin main
check "failing test: refused" "$([ "$rc" != 0 ] && echo refused)" refused
check "failing test: the remote stays" "$(head_of "$R")" "$before"
has "failing test: named" "$out" "tools/bad.test.sh"
has "failing test: its output is shown" "$out" "boom"

fresh
put tools/ok.test.sh "$GOOD"
put tools/guard.py "import sys
sys.exit(1 if '--selftest' in sys.argv else 0)"
ci selftest
push origin main
check "failing selftest: refused" "$([ "$rc" != 0 ] && echo refused)" refused
has "failing selftest: named" "$out" "tools/guard.py"

fresh
put tools/ok.test.sh "$GOOD"
put tools/plain.py "raise SystemExit(1)"
ci plain
push origin main
check "a python file without a selftest is not a test" "$rc" 0

fresh
put tools/ok.test.sh "$GOOD"; ci good
put tools/ok.test.sh "$BAD"
put tools/new.test.sh "$BAD"
push origin main
check "changes on disk do not decide the push" "$rc" 0

fresh
put tools/ok.test.sh "$BAD"; ci bad
put tools/ok.test.sh "$GOOD"
push origin main
check "a fix left on disk does not pass a broken commit" "$([ "$rc" != 0 ] && echo refused)" refused

fresh
put tools/ok.test.sh "$GOOD"; ci good
git -C "$W" branch side
push origin main side
put tools/ok.test.sh "touch '$T/ran'; exit 1"; ci bad
push origin --delete side
check "deleting a branch runs nothing" "$rc|$([ -e "$T/ran" ] && echo ran)" "0|"

fresh
put tools/hang.test.sh "sleep 20"; ci hang
start=$SECONDS
out=$(PRE_PUSH_LIMIT=1 git -C "$W" push origin main 2>&1 </dev/null); rc=$?
check "a stuck test ends as a failure" "$([ "$rc" != 0 ] && echo refused)" refused
check "a stuck test does not hold the push" "$([ $((SECONDS - start)) -lt 10 ] && echo fast)" fast
has "a stuck test: named" "$out" "tools/hang.test.sh"

fresh
put README "no tests here"; ci empty
push origin main
check "no tests found is a failure, not a pass" "$([ "$rc" != 0 ] && echo refused)" refused

fresh
put tools/ok.test.sh "cat >/dev/null; exit 0"; ci good
git -C "$W" checkout -q -b zz
put tools/bad.test.sh "$BAD"; ci bad
git -C "$W" checkout -q main
push origin main zz
check "every ref is tested" "$([ "$rc" != 0 ] && echo refused)" refused
has "every ref is tested: the second one named" "$out" "tools/bad.test.sh"

fresh
put tools/ok.test.sh "$GOOD"; ci good
put tools/bad.test.sh "$BAD"; ci bad
git -C "$W" tag -a v1 -m v1
git -C "$W" reset -q --soft HEAD~1
push origin v1
check "a tag on a broken commit: refused" "$([ "$rc" != 0 ] && echo refused)" refused

fresh
put tools/leak.test.sh 'cd "$(mktemp -d)" && git init -q && git config hooked.leak yes'; ci leak
out=$(GIT_DIR="$W/.git" GIT_WORK_TREE="$W" git -C "$W" push origin main 2>&1 </dev/null); rc=$?
check "a test's git stays out of the real repo" "$rc|$(git -C "$W" config hooked.leak)" "0|"

fresh
put tools/env.test.sh '[ -z "${HERDR_PANE_ID:-}${CLAUDECODE:-}${CODEX_THREAD_ID:-}${MUNICE_AUTO_DISPATCH:-}" ]'; ci env
out=$(HERDR_PANE_ID=w1:p1 CLAUDECODE=1 CODEX_THREAD_ID=x MUNICE_AUTO_DISPATCH=1 git -C "$W" push origin main 2>&1 </dev/null); rc=$?
check "the session's own variables never reach a test" "$rc" 0

fresh
put tools/ok.test.sh "$GOOD"; ci good
mkdir -p "$T/bin"
printf '#!/bin/sh\nexit 123\n' > "$T/bin/xargs"
chmod +x "$T/bin/xargs"
out=$(PATH="$T/bin:$PATH" git -C "$W" push origin main 2>&1 </dev/null); rc=$?
check "tests that never started refuse the push" "$([ "$rc" != 0 ] && echo refused)" refused
check "tests that never started: the remote stays" "$(head_of "$R")" ""

fresh
put tools/ok.test.sh "$GOOD"
put tools/guard.py "# handles --selftest"; ci nopython
mkdir -p "$T/nopy"
for t in git bash awk sort mktemp tar find grep perl xargs wc tr tail mkdir rm cat env; do
  ln -sf "$(command -v "$t")" "$T/nopy/$t"
done
out=$(PATH="$T/nopy" git -C "$W" push origin main 2>&1 </dev/null); rc=$?
check "a selftest that cannot start is a failure" "$([ "$rc" != 0 ] && echo refused)" refused
has "a selftest that cannot start: named" "$out" "tools/guard.py"

fresh
put tools/ok.test.sh "$GOOD"; ci good
git -C "$W" push -q origin main 2>/dev/null </dev/null
old=$(git -C "$W" commit-tree -m old "$(git -C "$W" mktree </dev/null)")
git -C "$W" worktree add -q -b old "$T/old" "$old"
mkdir -p "$T/old/tools"
printf '%s\n' "$BAD" > "$T/old/tools/bad.test.sh"
git -C "$T/old" add -A && git -C "$T/old" commit -q -m bad
out=$(git -C "$T/old" push origin old 2>&1 </dev/null); rc=$?
check "a worktree on a branch older than the hook is still tested" "$([ "$rc" != 0 ] && echo refused)" refused

fresh
put 'tools/say"hi.test.sh' "$BAD"; ci quoted
push origin main
check "a quote in a test's name: refused, not skipped" "$([ "$rc" != 0 ] && echo refused)" refused

fresh
put tools/ok.test.sh "python3 -c 'import tempfile; tempfile.mkdtemp()'"; ci temp   # macOS mktemp -d ignores TMPDIR
mkdir -p "$T/tmpd"
out=$(TMPDIR="$T/tmpd" git -C "$W" push origin main 2>&1 </dev/null); rc=$?
check "nothing is left in the temp folder" "$rc|$(ls -A "$T/tmpd")" "0|"

fresh
put tools/ok.test.sh "$GOOD"; ci good
push origin 'HEAD@{0 seconds ago}:refs/heads/x'
check "a ref written with spaces is read correctly" "$rc" 0

[ "$fails" = 0 ] && echo "all passed"
exit "$fails"
