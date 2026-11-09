#!/usr/bin/env bash
set -euo pipefail
python3 -I "$(dirname "$0")/test_agent_quota.py"
