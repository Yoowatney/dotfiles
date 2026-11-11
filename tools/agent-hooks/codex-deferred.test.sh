#!/usr/bin/env bash
# Include the recovery regression suite in the pre-push test discovery.
exec python3 -I "$(dirname "$0")/codex-deferred.test.py"
