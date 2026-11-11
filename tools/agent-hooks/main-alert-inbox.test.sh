#!/bin/bash
set -euo pipefail
exec python3 -I "$(dirname "$0")/main-alert-inbox.test.py"
