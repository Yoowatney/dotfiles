#!/bin/bash
set -euo pipefail
exec python3 -I "$(dirname "$0")/main-user-relay.test.py"
