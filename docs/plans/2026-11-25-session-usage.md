# Session usage observability

Goal: rank visible conversation characters at each main-session restart and
monitor GitHub quota with low-cost, explicitly approximate caller attribution.

Architecture: one standard-library module reads local transcript data. Existing
handoff hooks record a transition in both interactive runtimes. The existing
minute watcher invokes a quota tick gated to five minutes. No new daemon,
credential wrapper, token metering, HTTP tracing, or deployment is introduced.

## Constraints

- Persist counts and identifiers, never message bodies, commands or credentials.
- Character counts exclude hidden context and are not token estimates.
- Count visible gh command invocations, not HTTP requests. Background jobs,
  REPL-hidden commands and other machines remain unattributed.
- Keep existing handoffs intact. Add only a short, source-ranked reduction hint;
  never discard user text, safety instructions or tool results automatically.
- One quota request per five minutes across processes, including failures;
  warn below 20%, once per resource/reset window. Retry failed delivery locally.
- No changes to CI/deploy watchers, sync authentication or reporting validators.
- No installation or merge in this change. PR and independent Claude review.

## Task 1: transcript accounting and restart integration

Write failing tests for both transcript formats, duplicate records, malformed
lines, content privacy, real handoff startup/clear transitions and nested-session
exclusion. Implement bounded streaming counts and a ranked reduction note.
Verify with the new suite and existing handoff suites. Expected: all pass;
original handoff remains present and each distinct restart has one record.

## Task 2: low-cost quota watcher

Write failing tests for five-minute gating, cross-process exclusion, failures,
resource thresholds, reset-aware deduplication, failed-delivery retry and visible
command attribution. Add the watcher to the existing minute tick. Verify with
the new suite and workers-watch selftest. Expected: one GET /rate_limit per
interval, no failed response represented as zero, no secret output persisted.

## Task 3: verification and review

Run targeted regression suites, compile/shell checks and an isolated smoke test
using real local transcript schemas. Commit, push once, open PR, then request
independent Claude review of the exact diff and evidence. Fix material findings
with a failing regression first. Leave deployment to the reviewed merge path.

## Review focus

1. Cross-runtime and nested startup identity; manual clear versus automatic clear.
2. Malformed/oversized transcript and quota response handling; no raw text leaks.
3. Duplicate or concurrent ticks and delivery failure across a reset window.
4. Attribution limits: no equation between gh invocations and consumed requests.
5. No destructive context reduction or changes to excluded tools.

## Progress

- Design chosen: visible characters and gh command counts only.
- Baseline and RED/GREEN evidence are kept outside the repository.
