#!/bin/sh
# Run on the source Mac. The app, not config sync, owns its remote CLI and skill.
set -eu

if [ "$#" -gt 1 ]; then
  printf 'Usage: sh %s [ssh-host]\n' "$0" >&2
  exit 2
fi
INSTALLER="${EGO_SKILL_DIR:-$HOME/.agents/skills/ego-browser}/scripts/install.sh"
if [ ! -r "$INSTALLER" ]; then
  printf 'Official ego-browser installer missing: %s\n' "$INSTALLER" >&2
  exit 2
fi
if [ "$#" -eq 1 ]; then
  REMOTE_HOST=$1
else
  IFS= read -r REMOTE_HOST < "$HOME/.config/herdr/remote-host"
fi
case "$REMOTE_HOST" in
  ''|-*) printf 'Invalid SSH host.\n' >&2; exit 2 ;;
esac

# The official installer skips downloading an existing app and opens it again.
# Do not run this inside a remote work pane or widen that pane's sandbox.
ssh "$REMOTE_HOST" sh -s < "$INSTALLER"
ssh "$REMOTE_HOST" 'exec "$HOME/.local/bin/ego-browser" --version'
RUNTIME_OUTPUT=$(ssh "$REMOTE_HOST" 'exec "$HOME/.local/bin/ego-browser" nodejs' 2>&1 <<'JS'
console.log('ego-browser ready');
JS
)
printf '%s\n' "$RUNTIME_OUTPUT"
if ! printf '%s\n' "$RUNTIME_OUTPUT" | /usr/bin/grep -Fxq 'ego-browser ready'; then
  printf 'App installed, but runtime is not ready. Complete first-run onboarding on the remote Mac, then rerun.\n' >&2
  exit 3
fi
