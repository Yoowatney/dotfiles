# Ordinary-session handoffs

Both Claude and Codex load a saved handoff at session start in the same herdr pane.
Codex creates the new session on its first regular message after `/new`; send a
short continuation message to trigger its SessionStart hook. The saved note is
then supplied as context, followed by a marked continuation prompt.
Save a Markdown prompt with `handoff save <file>`. Include exactly one fenced
`handoff` JSON object. The main pane's automatic handoff keeps its existing format.

Required fields describe the task, completed work, remaining work, verification
evidence, and where/how to resume. Write explicit "none" or "not verified" instead
of leaving a field empty. Keep the useful narrative outside the JSON as needed.

```handoff
{
  "task": "Describe the task",
  "completed": "Describe completed work",
  "remaining": "Describe unfinished work or none",
  "verification": "Commands/results and absolute evidence paths, or not verified",
  "resume": "First action and any condition that must be checked before it",
  "code": "repositories",
  "repositories": [
    {
      "path": "/absolute/checkout/root",
      "verified_commit": null,
      "verified_dirty": null,
      "evidence": "Not verified"
    }
  ]
}
```

For work without code, use `"code": "none", "repositories": []`.
Otherwise list every relevant checkout separately, including different worktrees.
The saver records each checkout's current branch, full commit and dirty flag.
`verified_commit` is the full hash **at verification time**; `verified_dirty` is
the dirty flag at that time. Never infer either from the current checkout. Use
null when unknown. Describe the actual verification and its evidence in `evidence`.
Use `not verified` as the explicit unverified evidence marker. It remains unverified
even if a commit and clean flag were also supplied. Case, surrounding whitespace
and a trailing period do not change that marker's meaning; other prose is not
automatically interpreted as proof that tests ran.

A changed branch/commit, missing checkout, any dirty state or unknown verification
produces a warning on read. A clean match only confirms code identity; environment,
configuration and external state still need checking. Old freeform memos remain
readable with an unverified warning. A handoff grants no new deployment or external
write permission. Do not put private handoff contents in this public repository.
Reading checks the required fields again, so a damaged or incomplete saved block
cannot become a verified handoff. This check never replaces the saved Git snapshot.

An optional private policy file at
`${XDG_CONFIG_HOME:-~/.config}/agent-hooks/handoff-policy.json` applies to both
ordinary and main-pane handoffs before saving:

```json
{
  "version": 1,
  "rules": [{
    "id": "example-ownership-rule",
    "pattern": "(?m)^Assignment: obsolete receiver$",
    "message": "Keep the current receiver responsible for relayed work."
  }]
}
```

Patterns use Python regular expressions. A matching rule or malformed policy
rejects the save and preserves the previous memo. Keep private policy contents
outside this public repository. A previously saved memo is still readable, with
an inline warning that rejected instructions must not be followed; the warning
also appears when a long memo is loaded by path. A missing policy file keeps the
existing behavior; a broken symlink is an error. Remove the policy file to undo
only this optional validation, leaving ordinary handoff validation intact.

A save larger than 16 KiB (16384 bytes, counted after the saver adds its Git snapshot) is refused with exit 1:
nothing is cut, the previous memo stays, and the error tells the saver to shorten it. Refusing rather than
truncating keeps the open-items tail intact, and a main-pane refresh that never gets a saved memo skips the `/clear`
instead of wiping the session. Keep long detail in a file and write its absolute path in the memo.

Outside herdr, saving refuses with exit 2 and never copies the prompt. Keep the file and choose its destination explicitly. The current
implementation retains handoffs for 24 hours and consumes a memo once by renaming
it to `.loaded`. Do not rely on a handoff as permanent storage.
