#!/bin/sh
# launchd's com.yoyoo.herdr-workers. The workers server takes the main config with herdr's own desktop banners off: 요유 never
# enters those panes, but an iTerm tab attached to the server would still get a banner for each of them.
# herdr reads one config per server and not the session folder (0.9.0, tried 2026-11-05), so the server gets its own copy.
# ponytail: the copy is made at start, so `herdr server reload-config` on workers does not see later edits to the main config.
session=${1:-workers}
dir=$HOME/.config/herdr/sessions/$session
mkdir -p "$dir"
sed 's/^delivery = .*/delivery = "off"/' "$HOME/.config/herdr/config.toml" > "$dir/config.toml"
HERDR_CONFIG_PATH=$dir/config.toml exec /opt/homebrew/opt/herdr/bin/herdr --session "$session" server
