#!/usr/bin/env bash
[ -n "$ZSH_VERSION" ] || exec zsh "$0" "$@"  # pre-push runs *.test.sh with bash; codex() is zsh
# codex() in shell/zsh/zshrc adds --approve-for-me, which Codex refuses together with --sandbox/--ask-for-approval/--yolo
# (2026-11-16: every pane-spawn Codex pane exited at launch). Run: bash tools/agent-hooks/codex-launch-args.test.sh
setopt err_exit
here=${0:A:h}
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
printf '#!/bin/sh\necho "$@"\n' >"$tmp/stub"; chmod +x "$tmp/stub"
printf '#!/bin/sh\n[ "$1" = which ] && echo "%s"; exit 0\n' "$tmp/stub" >"$tmp/mise"; chmod +x "$tmp/mise"
export PATH="$tmp:$PATH" HERDR_CODEX_STARTUP_ROLE= HERDR_CODEX_STARTUP_HELPER=/nonexistent
eval "$(sed -n '/^codex() {/,/^}/p' "$here/../../shell/zsh/zshrc")"
unset HERDR_ENV
check() { # check <expect: has|no> <args...>
  local want=$1 out; shift
  out=$(cd "$tmp" && codex "$@")
  if [[ $want == has ]]; then [[ $out == *--approve-for-me* ]] || { print -u2 "FAIL: expected --approve-for-me for: $*  got: $out"; exit 1 }
  else [[ $out != *--approve-for-me* ]] || { print -u2 "FAIL: unexpected --approve-for-me for: $*  got: $out"; exit 1 }; fi
}
check has
check has resume
check no --sandbox danger-full-access --ask-for-approval never
check no --sandbox=read-only
check no -a never
check no --yolo
check no --pane-spawn-args
check no --dangerously-bypass-approvals-and-sandbox
[[ $(codex --approve-for-me) == *"--approve-for-me"*"--approve-for-me"* ]] && { print -u2 "FAIL: duplicated --approve-for-me"; exit 1 }
# Codex sessions open in Fast mode (요유 2026-11-23, #0): every launch carries service_tier="fast", a caller's own later -c wins.
for args in "" "resume" "--sandbox danger-full-access --ask-for-approval never" "--yolo"; do
  out=$(cd "$tmp" && codex ${=args})
  [[ $out == *'-c service_tier="fast"'* ]] || { print -u2 "FAIL: expected Fast mode for: $args  got: $out"; exit 1 }
done
out=$(cd "$tmp" && codex -c 'service_tier="default"')
[[ $out == *'service_tier="fast"'*'service_tier="default"' ]] || { print -u2 "FAIL: a caller's own service_tier must come last: $out"; exit 1 }
print ok
