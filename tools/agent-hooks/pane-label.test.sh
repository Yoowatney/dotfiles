#!/usr/bin/env bash
set -euo pipefail

SUBJECT="$(cd "$(dirname "$0")" && pwd)/pane-label"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

# A fake herdr that keeps the pane's label in $SANDBOX/label:
# `pane get <id>` prints it, `pane rename <id> <text>` sets it, `pane rename <id> --clear` removes it.
cat > "$SANDBOX/herdr" <<EOF
#!/usr/bin/env bash
case "\$1 \$2" in
  "pane get")
    if [ -f "$SANDBOX/label" ]; then jq -n --rawfile l "$SANDBOX/label" '{result: {pane: {label: \$l}}}'
    else echo '{"result":{"pane":{}}}'; fi ;;
  "pane rename")
    if [ "\$4" = "--clear" ]; then rm -f "$SANDBOX/label"; else printf '%s' "\$4" > "$SANDBOX/label"; fi ;;
  *) exit 2 ;;
esac
EOF
chmod +x "$SANDBOX/herdr"

pl() { env HERDR_PANE_ID=w1:p1 HERDR_BIN_PATH="$SANDBOX/herdr" bash "$SUBJECT" "$@"; }
label() { cat "$SANDBOX/label" 2>/dev/null || true; }
expect() { [ "$(label)" = "$1" ] || fail "$2: expected [$1], got [$(label)]"; }

# Outside herdr: nothing happens, exit 0.
env -u HERDR_PANE_ID HERDR_BIN_PATH="$SANDBOX/herdr" bash "$SUBJECT" "x" || fail "outside herdr should exit 0"
[ ! -f "$SANDBOX/label" ] || fail "outside herdr should not rename"

# No argument: usage, exit 2.
if pl 2>/dev/null; then fail "no argument should exit 2"; fi

# A new name starts 🟢: 요유 may close the session.
pl "api#1 첫 일"; expect "🟢 api#1 첫 일" "first name"

# --hold marks 🔴 and keeps the name; a rename keeps 🔴 while something still runs.
pl --hold; expect "🔴 api#1 첫 일" "hold"
pl "api#2 새 일"; expect "🔴 api#2 새 일" "rename keeps 🔴"

# --free goes back to 🟢, keeping the name.
pl --free; expect "🟢 api#2 새 일" "free"

# A caller that writes the mark itself is taken at its word, never doubled.
pl "🔴 api#3 배포 감시"; expect "🔴 api#3 배포 감시" "explicit 🔴"
pl "🟢 api#3 배포 감시"; expect "🟢 api#3 배포 감시" "explicit 🟢"

# --clear-if-prefix compares the name without its mark.
pl --clear-if-prefix "api#9 "; expect "🟢 api#3 배포 감시" "other prefix keeps the label"
pl --clear-if-prefix "api#3 "; expect "" "own prefix clears"

# --hold on an unnamed pane still shows the mark.
pl --hold; expect "🔴" "hold without a name"
pl "api#4 일"; expect "🔴 api#4 일" "naming after hold keeps 🔴"

echo "ok: pane-label"
