#!/usr/bin/env bash
# PreToolUse(Bash) hook: block `git push` when the commits it would send add
# secret-looking strings. Reports file + pattern name only, never the value.
# Fails open (exit 0) on anything it cannot parse, so unrelated Bash calls never break.
#
# ponytail: a speed bump for the pushes Claude actually runs, not a guarantee. It reads the
# command text, so it cannot see pushes made inside scripts (deploy scripts, `gh repo create
# --push`). Git's own pre-push hook would be exact, but husky repos set a local
# core.hooksPath that overrides a global one. Known misses are marked `ponytail:` below.
set -uo pipefail

NAMES=(
    "AWS access key"
    "GitHub token"
    "Anthropic key"
    "OpenAI key"
    "Slack token"
    "Slack webhook"
    "Discord webhook"
    "Google API key"
    "Stripe live key"
    "Private key"
    "GCP service account key"
    "Notion token"
)
REGEXES=(
    'AKIA[0-9A-Z]{16}'
    '(gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})'
    'sk-ant-[A-Za-z0-9_-]{20,}'
    '(^|[^A-Za-z0-9_-])sk-(proj-)?[A-Za-z0-9_-]{32,}'
    'xox[abposr]-[A-Za-z0-9-]{10,}'
    'hooks\.slack\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[A-Za-z0-9]+'
    'discord(app)?\.com/api/webhooks/[0-9]+/[A-Za-z0-9_-]+'
    'AIza[0-9A-Za-z_-]{35}'
    '[rs]k_live_[0-9A-Za-z]{20,}'
    '-----BEGIN ([A-Z]+ )*PRIVATE KEY( BLOCK)?-----'
    '"private_key_id"[[:space:]]*:'
    'ntn_[A-Za-z0-9]{40,}'
)
COMBINED=$(IFS='|'; echo "${REGEXES[*]}")
TAB=$'\t'
NL=$'\n'

payload=$(cat)
[[ "$payload" == *push* ]] || exit 0
cmd=$(printf '%s' "$payload" | jq -r '.tool_input.command // empty' 2>/dev/null) || exit 0
[[ -n "$cmd" ]] || exit 0
cwd=$(printf '%s' "$payload" | jq -r '.cwd // empty' 2>/dev/null)
[[ -n "$cwd" ]] || cwd="$PWD"

US=$'\x1f'
unquote() { UQ=${1//\"/}; UQ=${UQ//\'/}; }
resolve() {  # $1 = path, $2 = base dir -> RES (a global, so the token loop spawns no subshells)
    RES="$1"
    [[ "$RES" == "~"* ]] && RES="$HOME${RES#\~}"
    [[ "$RES" == /* ]] || RES="$2/$RES"
}

# Split the command into simple commands, one per line, tokens joined by \x1f.
# shlex keeps quoted text whole, so `echo "a && git push"` stays one echo.
# Commands handed to `bash -c`/`sh -c`/`eval` are parsed as commands too.
read -r -d '' SPLIT_PY <<'PY'
import os, shlex, sys
PUNCT = ";&|()\n"
def parse(s, depth=0):
    lex = shlex.shlex(s, posix=True, punctuation_chars=PUNCT)
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    lex.commenters = ""
    segs, seg = [], []
    for t in lex:
        if t and all(c in PUNCT for c in t):
            if seg:
                segs.append(seg)
            seg = []
        else:
            seg.append(t)
    if seg:
        segs.append(seg)
    out = []
    for seg in segs:
        out.append(seg)
        if depth >= 3:
            continue
        if os.path.basename(seg[0]) in ("bash", "sh", "zsh"):
            for k, t in enumerate(seg[1:-1], 1):
                if t.startswith("-") and not t.startswith("--") and "c" in t:
                    out.extend(parse(seg[k + 1], depth + 1))
                    break
        elif seg[0] == "eval":
            out.extend(parse(" ".join(seg[1:]), depth + 1))
    return out
try:
    segs = parse(sys.stdin.read())
except ValueError:
    sys.exit(3)
for seg in segs:
    print("\x1f".join(t.replace("\n", " ") for t in seg))
PY
# ponytail: fallback for unbalanced quotes (a heredoc body with an apostrophe) or no python3:
# split on separators even inside quotes, which can over-detect but never hides a push.
naive_split() {
    local segs seg sep t out
    local -a w=()
    segs=${cmd//&&/$NL}; segs=${segs//||/$NL}
    for sep in ';' '|' '(' ')'; do segs=${segs//"$sep"/$NL}; done
    while IFS= read -r seg; do
        read -ra w <<< "$seg"; out=""
        for t in ${w[@]+"${w[@]}"}; do unquote "$t"; out="$out$US$UQ"; done
        printf '%s\n' "${out#"$US"}"
    done <<< "$segs"
}
seglines=$(printf '%s' "$cmd" | python3 -c "$SPLIT_PY" 2>/dev/null) || seglines=$(naive_split)

# Follow `cd`, then read the first git call of each simple command.
# ponytail: a `cd` inside a subshell leaks into later commands, and `cd -` is not followed.
REDIR_ONLY='^[0-9]*[<>]{1,2}$'
REDIR_ATTACHED='^[0-9]*[<>]'
pushes=()   # "<dir>\t<push args>"
has_commit=0
cur="$cwd"
while IFS= read -r line; do
    IFS="$US" read -ra w <<< "$line"
    n=${#w[@]}
    (( n )) || continue
    first="${w[0]##*/}"
    [[ "$first" == "#"* || "$first" == echo || "$first" == printf ]] && continue
    i=0
    while (( i < n )); do
        t="${w[i]}"
        [[ "$t" == "#"* ]] && break
        if [[ "$t" == cd ]]; then
            if (( i + 1 < n )) && [[ "${w[i+1]}" != "#"* ]]; then
                resolve "${w[i+1]}" "$cur"; cur="$RES"; i=$((i + 2))
            else
                cur="$HOME"; i=$((i + 1))
            fi
            continue
        fi
        if [[ "$t" == git || "$t" == */git ]]; then
            gdir="$cur"; sub=""; i=$((i + 1))
            while (( i < n )); do
                case "${w[i]}" in
                    -C) (( i + 1 < n )) && { resolve "${w[i+1]}" "$gdir"; gdir="$RES"; }; i=$((i + 2)) ;;
                    -c) i=$((i + 2)) ;;
                    -*) i=$((i + 1)) ;;
                    *) sub="${w[i]}"; i=$((i + 1)); break ;;
                esac
            done
            rest=""
            while (( i < n )); do
                t="${w[i]}"; i=$((i + 1))
                [[ "$t" == "#"* ]] && break
                # Redirections belong to the shell, not to git: `> log`, `2>/dev/null`.
                if [[ "$t" =~ $REDIR_ONLY ]]; then i=$((i + 1)); continue; fi
                [[ "$t" =~ $REDIR_ATTACHED ]] && continue
                rest="$rest $t"
            done
            [[ "$sub" == push ]] && pushes+=("$gdir$TAB$rest")
            [[ "$sub" == commit ]] && has_commit=1
            break
        fi
        i=$((i + 1))
    done
done <<< "$seglines"

(( ${#pushes[@]} )) || exit 0

# Commands run after this hook, so a commit made by the same call does not exist yet,
# and neither do files an earlier part of the call writes. Only a separate push can be scanned.
if (( has_commit )); then
    echo "secret-scan: run \`git commit\` and \`git push\` as separate Bash calls, so the push can be scanned once the commit exists." >&2
    exit 2
fi

# Fixed quoting and prefixes so "+++ b/<path>" headers parse whatever the user's git config says.
GITC=(-c core.quotePath=false -c diff.noprefix=false -c diff.mnemonicPrefix=false)

# Diff on stdin -> "<path>\t<added line>" for added lines only. "+++ " is a file header only
# between a "diff " line and the first "@@" hunk; inside a hunk it is an added "++ ..." line.
added_lines() {
    local file="" line header=0
    while IFS= read -r line; do
        case "$line" in
            "diff "*) header=1 ;;
            "@@"*) header=0 ;;
            "+++ "*)
                if (( header )); then
                    file="${line#+++ }"; [[ "$file" == /dev/null ]] && file=""
                    file="${file#\"}"; file="${file%\"}"; file="${file#b/}"
                else
                    printf '%s\t%s\n' "$file" "${line:1}"
                fi
                ;;
            "+"*) (( header )) || printf '%s\t%s\n' "$file" "${line:1}" ;;
        esac
    done
}

# The helpers below run inside scan_push and append to its local `refs` (bash locals are
# dynamically scoped). They read its `dir` and `remote`.
add_src() {  # refspec -> its source ref(s); wildcards are expanded against local refs
    local s="${1#+}" r
    s="${s%%:*}"
    [[ -n "$s" ]] || return 0
    if [[ "$s" == *"*"* ]]; then
        while IFS= read -r r; do
            # shellcheck disable=SC2053  # unquoted on purpose: glob match, * crosses "/" like git's
            [[ "$r" == $s || "$r" == refs/heads/$s ]] && refs+=("$r")
        done < <(git -C "$dir" for-each-ref --format='%(refname)' 2>/dev/null)
    else
        git -C "$dir" rev-parse --verify --quiet "$s^{commit}" >/dev/null && refs+=("$s")
    fi
}
# ponytail: matches against remote-tracking refs, so a branch someone else created on the
# remote since the last fetch is missed; checking for real needs a network `git ls-remote`.
add_matching() {
    local b
    while IFS= read -r b; do
        git -C "$dir" show-ref --verify --quiet "refs/remotes/$remote/$b" && refs+=("refs/heads/$b")
    done < <(git -C "$dir" for-each-ref --format='%(refname:short)' refs/heads 2>/dev/null)
}

scan_push() {  # $1 = dir the push runs in, $2 = push args -> "<path>\t<added line>" hits
    local dir remote="" word branch s cfg positional=0 explicit=0 deleting=0 i=0 n
    local -a args=() refs=() srcs=() notrefs=()
    dir=$(git -C "$1" rev-parse --show-toplevel 2>/dev/null) || return 0
    read -ra args <<< "$2"
    n=${#args[@]}
    while (( i < n )); do
        word="${args[i]}"; i=$((i + 1))
        case "$word" in
            --all|--branches) refs+=(--branches); explicit=1 ;;
            --mirror) refs+=(--all); explicit=1 ;;
            --tags) refs+=(--tags); explicit=1 ;;
            # --follow-tags only adds tags on commits the push already sends: nothing new to scan.
            -d|--delete) deleting=1 ;;
            --repo) remote="${args[i]-}"; i=$((i + 1)) ;;
            --repo=*) remote="${word#--repo=}" ;;
            --receive-pack|--exec|-o|--push-option) i=$((i + 1)) ;;
            -*) ;;
            *)
                positional=$((positional + 1))
                (( positional == 1 )) && { remote="$word"; continue; }
                explicit=1
                (( deleting )) || srcs+=("$word")
                ;;
        esac
    done

    branch=$(git -C "$dir" symbolic-ref --short -q HEAD 2>/dev/null)
    if [[ -z "$remote" ]]; then
        remote=$(git -C "$dir" config "branch.$branch.pushRemote" 2>/dev/null \
            || git -C "$dir" config remote.pushDefault 2>/dev/null \
            || git -C "$dir" config "branch.$branch.remote" 2>/dev/null \
            || echo origin)
    fi
    for s in ${srcs[@]+"${srcs[@]}"}; do add_src "$s"; done
    if (( ! explicit )); then
        # A bare push sends remote.<name>.push if set, else what push.default picks.
        cfg=$(git -C "$dir" config --get-all "remote.$remote.push" 2>/dev/null)
        if [[ -n "$cfg" ]]; then
            while IFS= read -r s; do
                if [[ "$s" == ":" || "$s" == "+:" ]]; then add_matching; else add_src "$s"; fi
            done <<< "$cfg"
        elif [[ "$(git -C "$dir" config push.default 2>/dev/null)" == matching ]]; then
            add_matching
        else
            refs+=(HEAD)
        fi
    fi
    (( ${#refs[@]} )) || return 0

    # Only the target remote's refs prove a commit is already there.
    # ponytail: a URL target has no tracking refs, so fall back to every remote.
    if git -C "$dir" remote 2>/dev/null | grep -Fxq -- "$remote"; then
        notrefs=(--not "--remotes=$remote")
    else
        notrefs=(--not --remotes)
    fi

    # --text: files marked binary in .gitattributes still get their lines scanned.
    git -C "$dir" "${GITC[@]}" log "${refs[@]}" "${notrefs[@]}" -p --cc -U0 --text \
        --no-color --no-ext-diff --no-textconv --format= 2>/dev/null \
        | grep -E -e '^(diff |@@|\+\+\+ )' -e "$COMBINED" | added_lines
}

# Split on the first tab by expansion: `IFS=$'\t' read` would drop an empty path field.
hits=$(for rec in "${pushes[@]}"; do scan_push "${rec%%"$TAB"*}" "${rec#*"$TAB"}"; done \
    | while IFS= read -r l; do
        path="${l%%"$TAB"*}"; content="${l#*"$TAB"}"
        [[ "$path" =~ $COMBINED ]] && path="(file name redacted: it looks like a secret)"
        for i in "${!REGEXES[@]}"; do
            if [[ "$content" =~ ${REGEXES[$i]} ]]; then
                printf '  %s: %s\n' "${path:-?}" "${NAMES[$i]}"
                break
            fi
        done
    done | sort -u)

[[ -z "$hits" ]] && exit 0
{
    echo "secret-scan: push blocked. What this push would send adds secret-looking strings (values not shown):"
    echo "$hits"
    echo "Remove them from the commits (amend or rebase locally; force-push is denied) and rotate any real secret."
    echo "If this is a false positive, ask the user to run the push themselves with \`! git push ...\`."
} >&2
exit 2
