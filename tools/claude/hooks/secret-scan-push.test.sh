#!/usr/bin/env bash
# Self-check for secret-scan-push.sh — runs against temp git repos.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
HOOK="$HERE/secret-scan-push.sh"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# Isolate git from the user's global config and hooks.
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.com
export GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.com

# Fake secrets are assembled at runtime so this file never contains one
# (dotfiles is public, and the hook would block pushing it otherwise).
AWS_KEY="AKIA""IOSFODNN7EXAMPLE"
OPENAI_KEY="sk-""proj-abcdefghijklmnopqrstuvwxyz0123456789ABCD"
SLACK_HOOK="https://hooks.slack.com/services/""T00000000/B00000000/XXXXXXXXXXXXXXXXXXXXXXXX"
PEM_HEADER="-----BEGIN ""RSA PRIVATE KEY-----"

new_repo() {  # $1 = name; prints the path of a clone whose base commit is already pushed
  local remote="$TMP/$1.git" repo="$TMP/$1"
  git init -q --bare "$remote"
  git clone -q "$remote" "$repo" 2>/dev/null
  echo base > "$repo/README.md"
  git -C "$repo" add README.md
  git -C "$repo" commit -qm base
  git -C "$repo" push -q origin HEAD 2>/dev/null
  echo "$repo"
}
commit_file() { printf '%s\n' "$3" > "$1/$2"; git -C "$1" add "$2"; git -C "$1" commit -qm "add $2"; }

run_hook() {  # $1 = cwd, $2 = command; sets RC and ERR
  local payload
  payload=$(jq -n --arg c "$2" --arg d "$1" '{tool_name:"Bash",tool_input:{command:$c},cwd:$d}')
  ERR=$(printf '%s' "$payload" | "$HOOK" 2>&1 >/dev/null) && RC=0 || RC=$?
}

fail=0
expect() { [ "$RC" = "$2" ] || { echo "FAIL: $1 — rc $RC, want $2. stderr: $ERR"; fail=1; }; }
has()    { [[ "$ERR" == *"$2"* ]] || { echo "FAIL: $1 — '$2' not in stderr: $ERR"; fail=1; }; }
hasnt()  { [[ "$ERR" != *"$2"* ]] || { echo "FAIL: $1 — secret value leaked into stderr"; fail=1; }; }

# --- detection basics ---

# a command that is not a push passes untouched
R=$(new_repo r1); commit_file "$R" secret.txt "key=$AWS_KEY"
run_hook "$R" "git status"; expect "non-push command" 0

# an outgoing commit with an AWS key blocks, naming file + pattern but never the value
run_hook "$R" "git push"; expect "outgoing AWS key" 2
has "outgoing AWS key" "secret.txt: AWS access key"
hasnt "outgoing AWS key" "$AWS_KEY"

# a clean outgoing commit passes
R=$(new_repo r2); commit_file "$R" notes.txt "hello"
run_hook "$R" "git push"; expect "clean outgoing commit" 0

# a secret already on the remote does not block a later clean push
R=$(new_repo r3); commit_file "$R" old.txt "key=$AWS_KEY"; git -C "$R" push -q origin HEAD 2>/dev/null
commit_file "$R" new.txt "clean"
run_hook "$R" "git push"; expect "secret already on remote" 0

# a bare push does not scan untracked files that are not being committed
R=$(new_repo r4); printf 'key=%s\n' "$AWS_KEY" > "$R/scratch.txt"
run_hook "$R" "git push"; expect "untracked file with bare push" 0

# ordinary words containing "sk-" are not OpenAI keys
R=$(new_repo r5); commit_file "$R" doc.md "see risk-assessment-for-the-whole-platform-rollout-plan-v2"
run_hook "$R" "git push"; expect "risk- word" 0

# every pattern is caught by both the grep prefilter and the bash matcher
R=$(new_repo r6)
commit_file "$R" p-aws "k=$AWS_KEY"
commit_file "$R" p-github "t=ghp_""abcdefghijklmnopqrstuvwxyz0123456789"
commit_file "$R" p-anthropic "t=sk-ant-""api03-abcdefghijklmnopqrstuvwxyz"
commit_file "$R" p-openai "OPENAI_API_KEY=$OPENAI_KEY"
commit_file "$R" p-slacktoken "t=xoxb-""1234567890-abcdefghij"
commit_file "$R" p-slackhook "URL=$SLACK_HOOK"
commit_file "$R" p-discord "u=https://discord.com/api/webhooks/""123456789012345678/abcdefghijklmnopqrstuvwxyz"
commit_file "$R" p-google "k=AIza""SyAbcdefghijklmnopqrstuvwxyz0123456"
commit_file "$R" p-stripe "k=sk_""live_abcdefghijklmnopqrstuvwx"
commit_file "$R" p-pem "$PEM_HEADER"
commit_file "$R" p-pgp "-----BEGIN ""PGP PRIVATE KEY BLOCK-----"
commit_file "$R" p-gcp '{"private_''key_id": "abc"}'
commit_file "$R" p-notion "t=ntn_""abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJ"
run_hook "$R" "git push"; expect "all patterns" 2
for want in "p-aws: AWS access key" "p-github: GitHub token" "p-anthropic: Anthropic key" \
            "p-openai: OpenAI key" "p-slacktoken: Slack token" "p-slackhook: Slack webhook" \
            "p-discord: Discord webhook" "p-google: Google API key" "p-stripe: Stripe live key" \
            "p-pem: Private key" "p-pgp: Private key" "p-gcp: GCP service account key" \
            "p-notion: Notion token"; do
    has "all patterns" "$want"
done

# non-ASCII file names: git quotes them by default, the secret must still be caught
R=$(new_repo r7); commit_file "$R" "한글.env" "key=$AWS_KEY"
run_hook "$R" "git push"; expect "non-ASCII file name" 2
has "non-ASCII file name" "한글.env: AWS access key"

# a file name that itself looks like a secret is redacted, never printed
R=$(new_repo r8); commit_file "$R" "$AWS_KEY.env" "OPENAI_API_KEY=$OPENAI_KEY"
run_hook "$R" "git push"; expect "secret-looking file name" 2
hasnt "secret-looking file name" "$AWS_KEY"; has "secret-looking file name" "redacted"

# --- which repo the push runs in ---

R=$(new_repo r9); commit_file "$R" s.env "key=$AWS_KEY"; CLEAN=$(new_repo r10)
run_hook "$CLEAN" "cd $R && git push origin HEAD"; expect "cd && push" 2
run_hook "$CLEAN" "git -C $R push"; expect "git -C push" 2
run_hook "$CLEAN" "cd $R; git push"; expect "cd ; push" 2
run_hook "$CLEAN" "(cd $R && git push)"; expect "subshell cd" 2
run_hook "$CLEAN" "cd $R
git push"; expect "multi-line cd" 2
run_hook "$CLEAN" "bash -c 'cd $R && git push'"; expect "bash -c" 2
# the directory belongs to the push, not to the first path in the command
run_hook "$TMP" "git -C $CLEAN status && git -C $R push"; expect "-C of the push itself" 2
run_hook "$TMP" "cd $CLEAN && git status && cd $R && git push"; expect "last cd before the push" 2
# from a subdirectory the push still scans the whole repo
mkdir -p "$R/sub"; run_hook "$R/sub" "git push"; expect "subdir cwd" 2

# --- what the push sends ---

# a stale local branch with a secret does not block pushing a clean HEAD...
R=$(new_repo r11)
git -C "$R" checkout -qb old; commit_file "$R" old.env "key=$AWS_KEY"; git -C "$R" checkout -q -
commit_file "$R" clean.txt "clean"
run_hook "$R" "git push"; expect "stale local branch" 0
# ...but naming it scans it, also as the second push of a compound command
run_hook "$R" "git push origin old"; expect "named branch push" 2
has "named branch push" "old.env: AWS access key"
run_hook "$R" "git push origin HEAD && git push origin old"; expect "second push in one command" 2

# tags: a tag pointing at an otherwise unreachable secret commit
R=$(new_repo r12)
git -C "$R" checkout -qb tmp; commit_file "$R" tag.env "key=$AWS_KEY"; git -C "$R" tag leak
git -C "$R" checkout -q -; git -C "$R" branch -qD tmp
run_hook "$R" "git push origin --tags"; expect "--tags" 2
run_hook "$R" "git push origin leak"; expect "named tag" 2

# push.default=matching sends every branch that also exists on the remote
R=$(new_repo r13)
git -C "$R" checkout -qb release; git -C "$R" push -q origin release 2>/dev/null
commit_file "$R" rel.env "key=$AWS_KEY"; git -C "$R" checkout -q -
git -C "$R" config push.default matching
run_hook "$R" "git push origin"; expect "push.default=matching" 2

# a secret already on another remote is still new to the remote being pushed to
R=$(new_repo r14); git init -q --bare "$TMP/r14-public.git"
git -C "$R" remote add public "$TMP/r14-public.git"; git -C "$R" push -q public HEAD:refs/heads/main 2>/dev/null
commit_file "$R" priv.env "key=$AWS_KEY"; git -C "$R" push -q origin HEAD 2>/dev/null
run_hook "$R" "git push public HEAD:main"; expect "other remote already has it" 2
run_hook "$R" "git push origin HEAD"; expect "same remote already has it" 0

# --- commit and push in one call ---

# the commit does not exist yet when the hook fires, so ask for two separate calls
R=$(new_repo r15)
run_hook "$R" "git add -A && git commit -m x && git push"; expect "add+commit+push" 2
has "add+commit+push" "separate"
run_hook "$R" "printf 'k=1\n' > a.env && git add a.env && git commit -m x && git push"; expect "file created then pushed" 2

# --- not a real push ---

R=$(new_repo r16); commit_file "$R" s.env "key=$AWS_KEY"
run_hook "$R" "echo 'git push'"; expect "echoed git push" 0
run_hook "$R" "# git push"; expect "commented git push" 0
run_hook "$R" "git status # then git push"; expect "trailing comment" 0
run_hook "$R" "git commit -m 'do not git push yet'"; expect "push inside a commit message" 0
# separators inside quotes are text, not command boundaries
run_hook "$R" "echo \"blocked: 'git add -A && git commit && git push'\""; expect "&& inside echoed quotes" 0
run_hook "$R" "git commit -m \"run tests && git push later\""; expect "&& inside a commit message" 0

# --- quoting and nested shells ---

R=$(new_repo "r17 with space"); commit_file "$R" s.env "key=$AWS_KEY"
run_hook "$TMP" "cd \"$R\" && git push"; expect "quoted path with spaces" 2
run_hook "$TMP" "bash -lc 'cd \"$R\" && git push'"; expect "bash -lc" 2
# an unbalanced quote (heredoc body with an apostrophe) falls back to the plain split
run_hook "$R" "cat <<EOF
it's done
EOF
git push"; expect "unbalanced quote fallback" 2

# --- push forms git understands but a naive parser misreads (Codex pass 2) ---

R=$(new_repo r18); commit_file "$R" s.env "key=$AWS_KEY"
run_hook "$R" "/bin/echo git push"; expect "echo called by path" 0
run_hook "$R" "git push > $TMP/push.log 2>&1"; expect "redirected output" 2
run_hook "$R" "git push --receive-pack git-receive-pack origin"; expect "option that takes a value" 2
run_hook "$R" "git push origin 'refs/heads/*:refs/heads/*'"; expect "wildcard refspec" 2
CLEAN=$(new_repo r19)
HOME="$R" run_hook "$CLEAN" "cd && git push"; expect "bare cd goes to HOME" 2

# an added line that itself starts with "++ " shows up as "+++ " in the diff
R=$(new_repo r20); printf '++ %s\n' "$AWS_KEY" > "$R/plus.txt"; git -C "$R" add plus.txt; git -C "$R" commit -qm plus
run_hook "$R" "git push"; expect "'+++ ' content line" 2

# files marked binary in .gitattributes are still read as text
R=$(new_repo r21); printf '*.env binary\n' > "$R/.gitattributes"
git -C "$R" add .gitattributes; git -C "$R" commit -qm attr; git -C "$R" push -q origin HEAD 2>/dev/null
commit_file "$R" leak.env "key=$AWS_KEY"
run_hook "$R" "git push"; expect "binary attribute" 2

# deleting a remote branch sends nothing, whatever the local branch of that name holds
R=$(new_repo r22); git -C "$R" checkout -qb gone; git -C "$R" push -q origin gone 2>/dev/null
commit_file "$R" g.env "key=$AWS_KEY"; git -C "$R" checkout -q -
run_hook "$R" "git push origin --delete gone"; expect "--delete" 0

# --follow-tags only adds tags on commits the push already sends
R=$(new_repo r23)
git -C "$R" checkout -qb t; commit_file "$R" t.env "key=$AWS_KEY"; git -C "$R" tag lt
git -C "$R" checkout -q -; git -C "$R" branch -qD t
run_hook "$R" "git push origin --follow-tags"; expect "--follow-tags with an unrelated tag" 0

# remote.<name>.push decides what a bare push sends
R=$(new_repo r24); git -C "$R" checkout -qb release; git -C "$R" push -q origin release 2>/dev/null
commit_file "$R" r.env "key=$AWS_KEY"; git -C "$R" checkout -q -
git -C "$R" config remote.origin.push refs/heads/release:refs/heads/release
run_hook "$R" "git push origin"; expect "remote.origin.push" 2

# push.default=matching does not send a new branch the remote lacks
R=$(new_repo r25); git -C "$R" config push.default matching
git -C "$R" checkout -qb feature; commit_file "$R" f.env "key=$AWS_KEY"
run_hook "$R" "git push origin"; expect "matching skips a branch the remote lacks" 0

# --- fail open ---

mkdir -p "$TMP/plain"; run_hook "$TMP/plain" "git push"; expect "not a git repo" 0
ERR=$(printf '{"tool_input":{"command":"git push"' | "$HOOK" 2>&1 >/dev/null) && RC=0 || RC=$?
expect "malformed JSON" 0

[ "$fail" = 0 ] && echo "PASS: secret-scan-push" || { echo "secret-scan-push: FAILED"; exit 1; }
