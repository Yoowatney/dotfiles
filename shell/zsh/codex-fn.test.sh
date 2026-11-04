#!/usr/bin/env bash
# The `codex` function in zshrc: `codex exec <prompt>` must not wait on an open
# non-TTY stdin (an agent's background shell leaves one open and the run hangs
# before it starts), while `codex exec -` and the TUI keep their stdin.
set -euo pipefail

ZSHRC="$(cd "$(dirname "$0")" && pwd)/zshrc"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

# A fake codex that says whether its stdin is /dev/null, then its arguments.
cat > "$SANDBOX/codex" <<'EOF'
#!/bin/sh
if [ /dev/fd/0 -ef /dev/null ]; then s=NULL; else s=OPEN; fi
echo "STDIN=$s ARGS=$*"
EOF
chmod +x "$SANDBOX/codex"
# A fake mise: `mise which codex` names the fake, `mise upgrade` does nothing.
cat > "$SANDBOX/mise" <<EOF
#!/bin/sh
[ "\$1" = which ] && echo "$SANDBOX/codex"
exit 0
EOF
chmod +x "$SANDBOX/mise"

run() { # codex args... -> what the fake codex printed, with an open pipe on stdin; HERDR=1 means inside herdr
    sleep 2 | env PATH="$SANDBOX:$PATH" HERDR_ENV="${HERDR:-}" \
        zsh -c "source <(sed -n '/^codex() {/,/^}/p' '$ZSHRC'); codex \"\$@\"" zsh "$@"
}

out=$(run exec -s read-only "say ok")
[[ $out == "STDIN=NULL ARGS=exec -s read-only say ok" ]] || fail "a prompt argument should close stdin: $out"
out=$(run exec -)
[[ $out == "STDIN=OPEN ARGS=exec -" ]] || fail "the stdin prompt marker should keep stdin: $out"
out=$(run exec review --uncommitted)
[[ $out == STDIN=OPEN* ]] || fail "a trailing option should keep stdin: $out"
out=$(run resume)
[[ $out == "STDIN=OPEN ARGS=resume" ]] || fail "a non-exec subcommand should keep stdin: $out"
out=$(HERDR=1 run exec "say ok")
[[ $out == "STDIN=NULL ARGS=--no-daemon exec say ok" ]] || fail "herdr should add --no-daemon first: $out"
echo "ok"
