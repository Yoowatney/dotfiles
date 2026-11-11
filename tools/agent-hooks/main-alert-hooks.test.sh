#!/usr/bin/env bash
# Entry point for the pre-push shell-test discovery.
set -euo pipefail
exec python3 -I "$(dirname "$0")/main-alert-hooks.test.py"
