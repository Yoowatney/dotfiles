# Optional local Stop checker

`yoyoo-share-guard.py` optionally reads `$XDG_CONFIG_HOME/agent-hooks/reaction-policy.json` (default `~/.config/agent-hooks/reaction-policy.json`). Keep private checker paths and domain rules in that local file, outside this repository.

```json
{"version": 1, "command": ["/path/to/python3", "-I", "/path/to/checker.py"], "timeout_seconds": 5}
```

The command is a nonempty argv list of nonempty strings, executed directly without a shell. The timeout must be a finite number from 1 through 5 seconds. Set a smaller internal budget in the checker so it can preserve partial progress before this timeout. Existing runtime hook registration and trust configuration are not changed by the installer.

Only a verified main pane runs the checker: the Stop session id must match that pane's agent session, and its terminal must be the current main terminal according to `herdr_peers.delegates_checks`. Workers, SDK calls and unverifiable identities retain their previous behavior. An absent policy also preserves previous behavior; a broken policy link is an invalid policy.

The checker receives the original Stop JSON on stdin and the hook environment. Exit 0 means verification or durable deferral succeeded. Its final stdout line may be a JSON object with `additional_context`; up to 1,500 characters are queued for the next prompt without another Stop continuation. A malformed policy, startup error, nonzero exit or timeout returns `decision: block` and a reason for both runtimes, including Claude's `--claude` mode. Failure details are limited to the last 1,500 characters of stderr, or stdout when stderr is empty. A timed-out checker process group is killed and reaped.

Reaction checks run in a joined thread alongside the existing checks. The ordinary `decide` then inbox order, reasons and recursive-Stop skip rules remain unchanged. Parallel execution avoids adding the checker budget to all existing checks. It does not guarantee a total runtime bound: existing checks, process startup and operating-system delays can still exceed an external timeout.

Retry state lives under `$XDG_STATE_HOME/agent-hooks/reaction-checks` (default `~/.local/state/agent-hooks/reaction-checks`). A session/turn may block for reaction failures at most three times, including recursive Stops. The third failure records a five-minute backoff and queues a warning for the next prompt. During backoff the checker is skipped. Later failures of the same turn warn and renew backoff without blocking; success resets the count. Private durable retry remains the checker's responsibility.

Turn identity uses an explicit `turn_id`, or the latest transcript input and its position, ignoring meta and sidechain messages. Without either, a stable per-session fallback still bounds retries. Retry state is locked, owner-checked and size-bounded. If it cannot be used, reaction checking defers with a warning instead of trapping the session in an unbounded loop; ordinary checks and their blocking reasons still apply.

Claude's [Stop decision protocol](https://code.claude.com/docs/en/hooks#stop-decision-control) supports the top-level block response. Runtime interruption, API failure, or the runtime's own continuation cap can still end a turn. This hook does not replace durable retry/repair in the private checker.

Activate a policy only after its checker and the reviewed common hook are installed. To roll back the local integration, remove its policy link. No-policy installations require no configuration change.
