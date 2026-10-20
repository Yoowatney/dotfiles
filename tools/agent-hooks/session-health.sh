#!/usr/bin/env bash
# SessionStart hook (Claude Code and Codex): turns two repeated lessons into checks.
#  1. A checkout far behind origin/main (local refs only) gets misread as "what is deployed".
#  2. MEMORY.md past ~25KB / 200 lines silently drops entries; warn well before that.
# Local only and fast: the network fetch runs detached so the NEXT session sees fresh counts.
# Prints nothing when healthy. Fail open: any error exits 0 silently.
# Env (tests): SESSION_HEALTH_NO_FETCH=1 skips the background fetch, SESSION_HEALTH_ROOT overrides the workspace root.
input=$(cat 2>/dev/null)
command -v jq >/dev/null 2>&1 || exit 0
BEHIND_MIN=20
MEMORY_WARN_BYTES=20000
ROOT=${SESSION_HEALTH_ROOT:-/Users/yoyoo/work/munice}

cwd=$(jq -r '.cwd // empty' <<<"$input" 2>/dev/null)
cwd=${cwd:-$PWD}
[ -d "$cwd" ] || exit 0
msgs=""
add() { msgs="${msgs:+$msgs$'\n'}$1"; }

ADVICE="배포된 동작은 origin/main이나 릴리스 태그에서 읽으세요."
behind=0 # set by check_repo: commits HEAD is behind origin/main (0 when unknown)
check_repo() { # $1 = repo dir
    local dir=$1 n
    behind=0
    # rev-list fails on a missing ref, so one call covers "exists" and "count".
    n=$(git -C "$dir" rev-list --count HEAD..refs/remotes/origin/main 2>/dev/null) ||
        n=$(git -C "$dir" rev-list --count HEAD..refs/remotes/origin/HEAD 2>/dev/null) || return 0
    behind=${n:-0}
    # Detached refresh for the next session; never touches the working tree or branch.
    if [ -z "${SESSION_HEALTH_NO_FETCH:-}" ]; then
        (nohup perl -e 'alarm 30; exec @ARGV' git -C "$dir" fetch --quiet origin >/dev/null 2>&1 &) >/dev/null 2>&1
    fi
}

if [ "$(cd "$cwd" && pwd -P)" = "$(cd "$ROOT" 2>/dev/null && pwd -P)" ]; then
    # Workspace root: one combined line; linked worktrees (.git is a file) and -wt- dirs are skipped.
    list=""
    for d in "$ROOT"/*/; do
        d=${d%/}
        [ -d "$d/.git" ] && [[ $d != *-wt-* ]] || continue
        check_repo "$d"
        [ "$behind" -ge "$BEHIND_MIN" ] && list="$list${d##*/} $behind"$'\n'
    done
    if [ -n "$list" ]; then
        total=$(printf '%s' "$list" | grep -c .)
        shown=$(printf '%s' "$list" | sort -k2,2nr | head -n 5 | sed 's/ \([0-9]*\)$/ \1/' | paste -sd, - | sed 's/,/, /g')
        more=""; [ "$total" -gt 5 ] && more=" 외 $((total - 5))개"
        add "origin/main보다 ${BEHIND_MIN}커밋 이상 뒤처진 체크아웃(마지막 fetch 기준): ${shown}${more}. $ADVICE"
    fi
elif top=$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null); then
    check_repo "$top"
    [ "$behind" -ge "$BEHIND_MIN" ] &&
        add "${top##*/} 체크아웃이 origin/main보다 ${behind}커밋 뒤입니다(마지막 fetch 기준). $ADVICE"
fi

mem="$HOME/.claude/projects/$(printf '%s' "$cwd" | sed 's/[^A-Za-z0-9]/-/g')/memory/MEMORY.md"
if [ -f "$mem" ]; then
    size=$(wc -c <"$mem" | tr -d ' ')
    if [ "${size:-0}" -gt "$MEMORY_WARN_BYTES" ] 2>/dev/null; then
        pretty=$(printf '%s' "$size" | rev | sed 's/[0-9]\{3\}/&,/g' | rev | sed 's/^,//')
        add "MEMORY.md가 ${pretty}B로 25KB 한도에 가깝습니다. 닫힌 이슈나 배포가 끝난 항목은 지우고, 긴 줄은 주제 파일로 옮기세요."
    fi
fi

[ -n "$msgs" ] && jq -n --arg ctx "$msgs" '{hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: $ctx}}' 2>/dev/null
exit 0
