# Claude-only checks (2026-11-19)

Goal: no check session opens on Codex, and the codex-review step leaves instructions.

1. pane-spawn: every `--check` is Claude whatever the runner policy or quota; `--check --kind codex` is refused;
   checks skip the quota lookup. Delete the Codex check sandbox, now unreachable (pane-spawn-check.rules,
   pane-spawn-mcp.py, the short-launch transport). Verify: every pane-spawn*.test.sh, test_auto_check_relay.py.
2. auto-tab-close.py keeps asking for `--kind auto`; pane-spawn turns it into Claude, so its permission retry
   (Claude only) never meets a Codex check. Verify: test_auto_check_relay.py.
3. agent-runner and docs/worker-quota-routing.md: the policy routes work sessions only.
4. Private instructions (rules, merge and dispatch skills, memory) drop codex-review in their own repository.
5. Live check: a new Claude and a new Codex session open their check on Claude and run no codex-review.

Not here: codex-startup.py still accepts the old check launch transport (its own tests cover it); remove with
the next change to that helper.
