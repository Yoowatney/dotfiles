#!/usr/bin/env bash
# Include the session-independent ledger suite in the pre-push test discovery.
exec python3 -I "$(dirname "$0")/task-ledger.test.py"
