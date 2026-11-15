# Optional local Stop checker

`yoyoo-share-guard.py` optionally reads `$XDG_CONFIG_HOME/agent-hooks/reaction-policy.json` (default `~/.config/agent-hooks/reaction-policy.json`). Keep private checker paths and domain rules in that local file, outside this repository.

```json
{"version": 1, "command": ["/path/to/python3", "-I", "/path/to/checker.py"], "timeout_seconds": 20}
```

The command is a nonempty argv list of nonempty strings, executed directly without a shell. The timeout must be a finite number from 1 through 20 seconds. Set the outer runtime hook timeout above the checker timeout plus identity and existing hook processing time; a 20-second checker needs at least 30 seconds externally.

Only a verified main pane runs the checker: the Stop session id must match that pane's agent session, and its terminal must be the current main terminal according to `herdr_peers.delegates_checks`. Workers, SDK calls and unverifiable identities retain their previous behavior. An absent policy also preserves previous behavior; a broken policy link is an invalid policy.

The checker receives the original Stop JSON on stdin and the hook environment. Exit 0 means verification or repair succeeded and produces no feedback. A malformed policy, startup error, nonzero exit or timeout returns `decision: block` and a reason for both runtimes, including Claude's `--claude` mode. Checker failure details are limited to the last 1,500 characters of stderr, or stdout when stderr is empty. A timed-out checker process group is killed. Repeated Stops still run the checker even with `stop_hook_active`; ordinary prose checks keep their existing once-per-turn behavior.

Claude's [Stop decision protocol](https://code.claude.com/docs/en/hooks#stop-decision-control) supports this top-level block response. Runtime interruption, API failure, or the runtime's own continuation cap can still end a turn; this hook does not replace durable retry/repair in the private checker.

To roll back the local integration, remove its policy link. No-policy installations require no configuration change.
