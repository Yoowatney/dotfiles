#!/bin/bash
# pre-push finds tests by the names *.test.sh and *.py; this command has no extension, so it runs through here.
exec python3 -I "$(dirname "$0")/decision-log" --selftest
