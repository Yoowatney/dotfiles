#!/usr/bin/env bash
# Starts the workers server script as a throwaway session on the real herdr: herdr's own banners are off there, the rest of the
# config is the main one. Never touches the real workers server.
set -uo pipefail
unset HERDR_SOCKET_PATH HERDR_PANE_ID HERDR_CONFIG_PATH
HERE="$(cd "$(dirname "$0")" && pwd)"
fails=0
check() { if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: expected [$3] got [$2]"; fails=1; fi; }
S="wtest-$$"
DIR="$HOME/.config/herdr/sessions/$S"
cleanup() { herdr session stop "$S" >/dev/null 2>&1; sleep 0.5; herdr session delete "$S" >/dev/null 2>&1; rm -rf "$DIR"; }
trap cleanup EXIT

PLIST="$HERE/com.yoyoo.herdr-workers.plist"
check "the plist runs the script last" "$(plutil -extract ProgramArguments json -o - "$PLIST" | jq -r '.[-2:] | join(" ")')" "/bin/sh $HOME/.dotfiles/tools/claude/herdr-workers-server.sh"
check "the plist has no KeepAlive" "$(v=$(plutil -extract KeepAlive raw -o - "$PLIST" 2>/dev/null) && printf '%s' "$v" || echo none)" none

# Start it the way launchd does (the plist's own command plus the session name), so the detach is tested too.
mapfile -t ARGS < <(plutil -extract ProgramArguments json -o - "$PLIST" | jq -r '.[]')
(nohup "${ARGS[@]}" "$S" >/dev/null 2>&1 &)
for _ in 1 2 3 4 5 6 7 8 9 10; do
  [ "$(herdr session list --json | jq -r --arg n "$S" '.sessions[] | select(.name == $n) | .running')" = true ] && break
  sleep 0.5
done
check "the server is a detached daemon (needed by herdr machine add)" "$(herdr --session "$S" status --json | jq -r '.server.capabilities.detached_server_daemon')" true
check "herdr's own notification is disabled on that server" "$(herdr --session "$S" notification show '임시 시험' --sound none | jq -r '.result.reason')" disabled
check "the copied config disables delivery once" "$(grep -c '^delivery = "off"$' "$DIR/config.toml")" "1"
diff <(sed 's/^delivery = .*/delivery = "off"/' "$HOME/.config/herdr/config.toml") "$DIR/config.toml" >/dev/null
check "the config matches main with delivery disabled" "$?" "0"
exit $fails
