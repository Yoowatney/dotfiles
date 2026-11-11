"""Build Codex check-pane MCP overrides without enabling write tools."""

import json
import os
import re
import sys
import tomllib

read_tools = {
    "slack": ["slack_list_channels", "slack_get_channel_history", "slack_get_thread_replies", "slack_get_users", "slack_get_user_profile"],
    "munice": ["ask_code", "ask_data"],
}
servers = tomllib.load(open(sys.argv[1], "rb")).get("mcp_servers", {}) if os.path.exists(sys.argv[1]) else {}
for name in servers:
    # Codex splits a -c path at every dot and keeps quotes as part of the name,
    # so mcp_servers."x" would create a different, broken server.
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        sys.exit(f"pane-spawn: -c cannot name the MCP server {name!r}")
    keep = read_tools.get(name)
    if not keep:
        print("-c", f"mcp_servers.{name}.enabled=false", sep="\n")
        continue
    # Only read tools remain. Approve them because a server may not advertise
    # their read-only annotation, which otherwise fails under approval=never.
    print("-c", f"mcp_servers.{name}.enabled_tools={json.dumps(keep)}", "-c", f'mcp_servers.{name}.default_tools_approval_mode="approve"', sep="\n")
