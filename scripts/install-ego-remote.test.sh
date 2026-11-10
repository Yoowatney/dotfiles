#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
TEST_DIR=$(mktemp -d)
trap 'rm -rf "$TEST_DIR"' EXIT HUP INT TERM
mkdir -p "$TEST_DIR/bin" "$TEST_DIR/skill/scripts"
printf '%s\n' '#!/bin/sh' 'exit 0' > "$TEST_DIR/skill/scripts/install.sh"
cat > "$TEST_DIR/bin/ssh" <<'SH'
#!/bin/sh
case "$*" in
  *'sh -s') cat >/dev/null; exit "${INSTALL_STATUS:-0}" ;;
  *--version*) echo 'ego-browser test'; exit 0 ;;
  *) cat >/dev/null; printf '%s\n' "$RUNTIME_OUTPUT" >&2 ;;
esac
SH
chmod +x "$TEST_DIR/bin/ssh"
export PATH="$TEST_DIR/bin:$PATH" EGO_SKILL_DIR="$TEST_DIR/skill"

check() {
  expected=$1
  export INSTALL_STATUS=$2 RUNTIME_OUTPUT=$3
  actual=0
  sh "$ROOT/scripts/install-ego-remote.sh" test-host > "$TEST_DIR/output" 2>&1 || actual=$?
  if [ "$actual" -ne "$expected" ]; then
    cat "$TEST_DIR/output"
    printf 'FAIL: expected exit %s, got %s\n' "$expected" "$actual" >&2
    exit 1
  fi
}

# The real CLI exits zero even when onboarding prevents JS from running.
check 3 0 'Please complete the onboarding process first.'
check 7 7 'ego-browser ready'
check 0 0 'ego-browser ready'
EGO_SKILL_DIR="$TEST_DIR/missing" check 2 0 'ego-browser ready'
printf '%s\n' 'PASS: onboarding is not readiness; install failures propagate; ready and missing-source states work.'
