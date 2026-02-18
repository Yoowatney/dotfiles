#!/bin/bash
# SSH key setup for a new machine
# Usage: ./setup-ssh.sh <IP> [USER]

HOST="${1:-}"
USER="${2:-yoyoo}"

if [[ -z "$HOST" ]]; then
    echo "Usage: ./setup-ssh.sh <IP> [USER]"
    echo "Example: ./setup-ssh.sh 192.168.123.154 yoyoo"
    exit 1
fi

echo "==> Adding host key for ${HOST}..."
ssh-keyscan -H "$HOST" >> ~/.ssh/known_hosts 2>/dev/null
echo "    Done."

echo "==> Copying SSH key to ${USER}@${HOST}..."
ssh-copy-id -o PubkeyAuthentication=no "${USER}@${HOST}"

echo ""
echo "==> Testing connection..."
if ssh "${USER}@${HOST}" "echo 'OK'; whoami; hostname; sw_vers 2>/dev/null; uname -m"; then
    echo ""
    echo "==> Connected. Ready to run: ./scripts/init-remote.sh ${HOST} ${USER}"
else
    echo ""
    echo "==> Connection failed."
    exit 1
fi
