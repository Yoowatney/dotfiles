#!/bin/bash
# Tests for codex-review-run, with a fake codex that records its arguments and stdin.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
RUN="$HERE/codex-review-run"
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
fails=0
ok() { echo "ok   $1"; }
bad() { echo "FAIL $1"; fails=$((fails + 1)); }
expect_rc() { [ "$2" = "$3" ] && ok "$1: exit $3" || bad "$1: exit $2, want $3"; }
has() { grep -qF -- "$3" "$2" && ok "$1: has '$3'" || { bad "$1: no '$3' in $2"; sed 's/^/     | /' "$2"; }; }
lacks() { grep -qF -- "$3" "$2" && bad "$1: unexpected '$3' in $2" || ok "$1: lacks '$3'"; }

cat >"$T/codex" <<'EOF'
#!/bin/bash
# fake codex: exec review <scope...> [-c developer_instructions=...] -o <file>
printf '%s\n' "$@" >"$FAKE_ARGS"
cat >"$FAKE_STDIN"
echo "pane=${HERDR_PANE_ID:-}" >"$FAKE_ENV"
out=
while [ $# -gt 0 ]; do [ "$1" = -o ] && out=$2; shift; done
f1='- [P1] Return the total, not the discount — /r/pricing.py:9-9'
f2='- [P2] Reject floats — /r/pricing.py:3-3'
f3='- [P3] Name the error — /r/pricing.py:4-4'
f3body='  ValueError("negative") does not say which argument.'
log() { printf '%s\n' "$@"; }
case $FAKE_MODE in
  clean) log exec 'cat pricing.py' codex 'No issues found.'; echo 'No issues found.' >"$out" ;;
  findings|lostone)
    # A real log repeats the final block, and Codex may cat a file holding finding-like lines earlier on.
    log exec '- [P0] an old line Codex read from some file' codex 'Looking.' codex 'Two problems.' '' 'Full review comments:' '' "$f1" '  body' "$f2" '  body'
    [ "$FAKE_MODE" = lostone ] && log "$f3" "$f3body" ''
    log 'tokens used' codex 'Full review comments:' '' "$f1" '  body' "$f2" '  body'
    [ "$FAKE_MODE" = lostone ] && log "$f3" "$f3body" ''
    printf '%s\n' 'Two problems.' '' 'Full review comments:' '' "$f1" '  body' "$f2" '  body' >"$out" ;;
  fail) echo 'error: not logged in' >&2; exit 7 ;;
  empty) log codex '' ;;
  oddformat) printf '%s\n' 'One problem.' '' '**[P1] Return the total** — /r/pricing.py:9' >"$out" ;;
  slow) sleep 5; echo 'late' >"$out" ;;
  slowchild) (sleep 2; touch "$FAKE_MARK") & sleep 10; echo 'late' >"$out" ;;
  firebase) touch firebase-debug.log; echo 'No issues found.' >"$out" ;;
esac
EOF
chmod +x "$T/codex"
export CODEX_BIN="$T/codex" FAKE_ARGS="$T/args" FAKE_STDIN="$T/stdin" FAKE_ENV="$T/env" FAKE_MARK="$T/child-ran"
# Codex follows the global rules and runs pane-label; with these it would rename (or mark free) the calling pane.
export HERDR_PANE_ID=pane-under-test HERDR_SOCKET_PATH="$T/no-socket"

repo="$T/repo"
git init -q -b main "$repo"
git -C "$repo" -c user.name=t -c user.email=t@example.invalid commit -q --allow-empty -m init
echo 'rule one' >"$T/rules.md"

run() { # run <name> <mode> <args...>: output in $T/<name>.out, exit code in $rc
  local name=$1 mode=$2; shift 2
  rc=0
  (cd "$repo" && echo junk | FAKE_MODE=$mode "$RUN" "$@") >"$T/$name.out" 2>&1 || rc=$?
}

run clean clean "$repo" uncommitted "$T/out"
expect_rc clean "$rc" 0
has clean "$T/clean.out" 'No issues found.'
has clean "$T/clean.out" "log: $T/out/codex-review-full.log"
has clean "$T/clean.out" 'findings: 0'
has clean-args "$T/args" '--uncommitted'
[ -s "$T/stdin" ] && bad 'clean: codex read stdin' || ok 'clean: codex stdin is empty'
has clean-file "$T/out/codex-verdict.md" 'No issues found.'
has clean-env "$T/env" 'pane='
lacks clean-env "$T/env" 'pane-under-test'

rc=0   # an output folder relative to the caller, not to the repo; an exported CDPATH makes cd print the path
(cd "$T" && CDPATH=. FAKE_MODE=clean "$RUN" "$repo" uncommitted rel-out </dev/null) >"$T/rel.out" 2>&1 || rc=$?
expect_rc relative "$rc" 0
has relative "$T/rel.out" "verdict: $T/rel-out/codex-verdict.md"
[ -s "$T/rel-out/codex-verdict.md" ] && ok 'relative: verdict written beside the caller' || bad 'relative: no verdict in the caller folder'

run findings findings "$repo" uncommitted "$T/out"
expect_rc findings "$rc" 1
has findings "$T/findings.out" 'findings: 2 (verdict 2, log 2)'
has findings "$T/findings.out" "verdict: $T/out/codex-verdict-2.md"
lacks findings "$T/findings.out" 'only in the log'
lacks findings "$T/findings.out" '[P0]'

run lostone lostone "$repo" uncommitted "$T/out"
expect_rc lostone "$rc" 1
has lostone "$T/lostone.out" 'findings: 3 (verdict 2, log 3)'
has lostone "$T/lostone.out" 'only in the log: - [P3] Name the error'
f3body='  ValueError("negative") does not say which argument.'   # as in the fake codex
has lostone "$T/lostone.out" "$f3body"   # the body too, so the finding can be relayed whole
[ "$(grep -cF -- "$f3body" "$T/lostone.out")" = 1 ] && ok 'lostone: the body is printed once' || bad 'lostone: body printed more than once'

# A [P#] tag in another layout must not end as "no findings".
run oddformat oddformat "$repo" uncommitted "$T/out"
expect_rc oddformat "$rc" 1
has oddformat "$T/oddformat.out" 'not in the "- [P#] " form'

run fail fail "$repo" uncommitted "$T/out"
expect_rc fail "$rc" 3
has fail "$T/fail.out" 'codex exited 7'
has fail "$T/fail.out" 'error: not logged in'

run empty empty "$repo" uncommitted "$T/out"
expect_rc empty "$rc" 3
has empty "$T/empty.out" 'wrote no verdict'

CODEX_REVIEW_LIMIT=1 run slow slow "$repo" uncommitted "$T/out"
expect_rc slow "$rc" 3
has slow "$T/slow.out" 'stopped after 1s'

CODEX_REVIEW_LIMIT=1 run slowchild slowchild "$repo" uncommitted "$T/out"
expect_rc slowchild "$rc" 3
sleep 3
[ -e "$FAKE_MARK" ] && bad 'slowchild: a child of codex kept running after the time limit' || ok 'slowchild: the time limit ended codex and its children'

run fb firebase "$repo" uncommitted "$T/out"
expect_rc fb "$rc" 0
[ -e "$repo/firebase-debug.log" ] && bad 'fb: firebase-debug.log left behind' || ok 'fb: firebase-debug.log removed'
has fb "$T/fb.out" 'removed firebase-debug.log'
touch "$repo/firebase-debug.log"
run fb-own firebase "$repo" uncommitted "$T/out"
[ -e "$repo/firebase-debug.log" ] && ok 'fb-own: an existing firebase-debug.log is kept' || bad 'fb-own: removed a file codex did not make'
rm -f "$repo/firebase-debug.log"

run commit clean "$repo" commit:abc123 "$T/out"
expect_rc commit "$rc" 0
has commit-args "$T/args" '--commit'
has commit-args "$T/args" 'abc123'

git init -q --bare "$T/origin.git"
git -C "$repo" remote add origin "$T/origin.git"
git -C "$repo" push -q origin main
run branch clean "$repo" branch "$T/out" "$T/rules.md"
expect_rc branch "$rc" 0
has branch-args "$T/args" '--base'
has branch-args "$T/args" 'origin/main'
has branch-args "$T/args" 'developer_instructions=rule one'

run norules clean "$repo" uncommitted "$T/out" "$T/missing.md"
expect_rc norules "$rc" 2
has norules "$T/norules.out" "$T/missing.md"

run badscope clean "$repo" everything "$T/out"
expect_rc badscope "$rc" 2

mkdir "$T/plain"
run notgit clean "$T/plain" uncommitted "$T/out"
expect_rc notgit "$rc" 2
has notgit "$T/notgit.out" 'not a git work tree'

CODEX_BIN="$T/nowhere" run nobin clean "$repo" uncommitted "$T/out"
expect_rc nobin "$rc" 3

[ "$fails" = 0 ] && echo "all passed" || { echo "$fails failed"; exit 1; }
