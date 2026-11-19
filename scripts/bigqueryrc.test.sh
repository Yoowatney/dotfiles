#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
RC="$ROOT/tools/bigquery/bigqueryrc"

fail() { echo "FAIL: $1" >&2; exit 1; }

[ -f "$RC" ] || fail "tools/bigquery/bigqueryrc is missing"
grep -qx '\[query\]' "$RC" || fail "bigqueryrc needs a [query] section"
# 20GB per job, in bytes
grep -qx -- '--maximum_bytes_billed=20000000000' "$RC" || fail "bigqueryrc needs the 20GB per-job cap"
grep -q 'create_symlink "\$DOTFILES/tools/bigquery/bigqueryrc" "\$HOME/.bigqueryrc"' "$ROOT/init.sh" \
  || fail "init.sh must link bigqueryrc to ~/.bigqueryrc"

echo "ok"
