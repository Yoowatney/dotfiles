#!/usr/bin/env bash
# Independent pane-tell group: queued delivery, alerts, and raw prompts.
# This entry runs only this group. The offline suite is pane-tell.test.sh and
# pane-tell-*.test.sh; pane-tell.live.test.sh remains an explicit live opt-in.
. "$(cd "$(dirname "$0")" && pwd)/pane-tell-test-common.sh"
tell_test_delivery
tell_test_finish
