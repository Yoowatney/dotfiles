#!/bin/bash
set -euo pipefail
exec python3 -I "$(dirname "$0")/pane-tell-batch.test.py" "$@"
