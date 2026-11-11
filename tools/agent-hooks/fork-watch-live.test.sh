#!/bin/bash
# Fixture-only regressions: this never starts the live runaway.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
exec /usr/bin/python3 -B -I "$HERE/fork-watch-live-tests.py"
