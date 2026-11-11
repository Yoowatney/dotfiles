#!/usr/bin/env bash
# Independent pane-tell group: Claude worker receiver batching.
# This entry runs only this group. The offline suite is pane-tell.test.sh and
# pane-tell-*.test.sh; pane-tell.live.test.sh remains an explicit live opt-in.
. "$(cd "$(dirname "$0")" && pwd)/pane-tell-test-common.sh"
tell_test_batch_setup
tell_test_peer_batch claude 🫡
tell_test_batch_done
tell_test_finish
