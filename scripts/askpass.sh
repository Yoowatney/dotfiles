#!/bin/bash
# SUDO_ASKPASS helper — echoes password from SUDO_PASS env var
# Used by init.sh for unattended .pkg cask installs
echo "$SUDO_PASS"
