#!/usr/bin/env bash
# gh-read runs outside a Codex check pane's sandbox (pane-spawn's rules), so whatever it lets through reaches GitHub with the
# user's token. It must pass GitHub reads and refuse every write, and run gh with nothing the agent could have set.
set -euo pipefail

SUBJECT="$(cd "$(dirname "$0")" && pwd)/gh-read"
fail() { echo "FAIL: $*" >&2; exit 1; }
reads() { # <args...>: gh-read passes them to gh
  local out
  out=$(GH_READ_DRY=1 "$SUBJECT" "$@" 2>&1) || fail "should read: gh $* -> $out"
  [[ "$out" == *" gh $*" ]] || fail "should run gh $* as given: $out"
}
refuses() { # <args...>: gh-read stops before gh
  local out
  if out=$(GH_READ_DRY=1 "$SUBJECT" "$@" 2>&1); then fail "should refuse: gh $* -> $out"; fi
  [[ "$out" == *"gh-read:"* ]] || fail "a refusal says why: $out"
  [[ "$out" != *"env -i"* ]] || fail "no gh runs on a refusal: $out"
}

reads issue view 12 -R owner/repo --comments
reads issue list -R o/r --state open --json number,title --jq '.[].title'
reads pr view 3 -R o/r --json files
reads pr diff 3 -R o/r
reads pr checks 3 -R o/r
reads run view 99 -R o/r --log-failed
reads run list -R o/r --workflow ci
reads release list -R o/r
reads repo view o/r
reads search issues "token 이슈" --owner owner
reads pr view https://github.com/o/r/pull/3
reads api repos/o/r/pulls/3
reads api repos/o/r/issues --paginate --jq '.[].number'
reads api -X GET search/issues -f q=repo:o/r
reads api --method=GET search/issues -F per_page=5

refuses pr merge 3 -R o/r
refuses pr comment 3 -R o/r -b hi
refuses pr close 3 -R o/r
refuses issue comment 12 -R o/r -b hi
refuses issue create -R o/r -t x
refuses issue edit 12 -R o/r --add-label x
refuses run rerun 99 -R o/r
refuses workflow run ci -R o/r
refuses release download v1 -R o/r
refuses repo clone o/r
refuses secret list -R o/r
refuses auth token
refuses alias set x 'pr merge'
refuses extension exec x
refuses
refuses api -X POST repos/o/r/issues
refuses api --method PATCH repos/o/r/issues/1
refuses api -XDELETE repos/o/r/git/refs/heads/x
refuses api repos/o/r/issues -f title=x # fields alone make gh POST
refuses api repos/o/r/issues --input body.json
refuses api graphql -f query='{ viewer { login } }' # a query document can hold a mutation
refuses api /graphql
refuses api repos/o/r -H 'X-HTTP-Method-Override: DELETE'
refuses api --hostname evil.example repos/o/r
refuses api https://evil.example/x
refuses pr view 3 --web # gh runs the browser command outside the sandbox
refuses issue list -w
refuses issue list -wR o/r
refuses pr view https://evil.example/o/r/pull/3
refuses pr view 3 -R evil.example/o/r
refuses pr view 3 --repo=evil.example/o/r
reads pr view 3 -R github.com/o/r

# gh starts from / with only the variables gh-read sets: no browser or pager command, no git config from a folder the agent
# can write, no other host.
out=$(BROWSER=/tmp/x GH_PAGER=/tmp/x GH_HOST=evil.example GIT_DIR=/tmp/x GH_READ_DRY=1 "$SUBJECT" issue view 1 -R o/r)
[[ "$out" == "cd / && env -i "* ]] || fail "gh runs from / with a clean environment: $out"
for v in BROWSER=/tmp GH_HOST GIT_DIR; do [[ "$out" != *"$v"* ]] || fail "$v must not reach gh: $out"; done
[[ "$out" == *"GH_PAGER=cat"* ]] || fail "gh's pager is cat: $out"
[[ "$out" == *"GH_PROMPT_DISABLED=1"* ]] || fail "gh asks nothing: $out"

echo "gh-read: all tests passed"
