# Antigravity worker integration

## Goal
Use subscription-authenticated Antigravity CLI for worker sessions while preserving Claude review sessions and existing safeguards. Never select paid API credentials or enable credit overages implicitly.

## Implementation
1. Add explicit AGY routing and simple/normal/complex model selection to pane-spawn.
2. Validate fresh subscription quota before opening a worker; refuse unknown, stale, exhausted or API-authenticated state.
   Refresh expired metadata through a bounded, input-free terminal session; accept only complete subscription windows and never submit a model prompt.
3. Translate native tool events into the existing shared guard interface. Unknown tools, malformed input and hook errors deny execution.
4. Load the shared instructions and canonical skills through native discovery. Reuse Ponytail's instruction generator.
5. Adapt native completion records to the existing report, deduplication and close sequence. Busy or failed turns cannot close a pane.

## Acceptance
- Unit and shell fixtures cover safe and denied calls, quota boundaries, model selection and report preservation.
- Native AGY calls prove secret-file and account-selection denial, normal command execution and hook-error denial.
- An actual pane-spawn worker verifies inherited authentication isolation, guard enforcement and report-before-close.
- Independent Claude review precedes changing the default worker policy.

## Rollback
Restore the previous local worker policy and settings snapshot, and remove only the AGY customization links introduced by this change. Existing Claude and Codex guards remain intact.

## Initial rollout constraints
- Open GCP CLI work and general fallback rechecks with `--kind codex`: AGY cannot write the gcloud credential cache within its terminal sandbox.
- Pinned Codex arguments and ambient-runner calls keep the Codex route even under the AGY default policy.
- Model tools cannot read or modify the subscription OAuth credential file. CLI authentication remains local.
- Install native customization and authenticate on each host before adopting the AGY default. Missing quota or customization must never trigger a paid fallback.
