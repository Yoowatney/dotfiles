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

A changed branch/commit, missing checkout, any dirty state or unknown verification
produces a warning on read. A clean match only confirms code identity; environment,
configuration and external state still need checking. Old freeform memos remain
readable with an unverified warning. A handoff grants no new deployment or external
write permission. Do not put private handoff contents in this public repository.

Outside herdr, saving still copies the prompt to the clipboard. The current
implementation retains handoffs for 24 hours and consumes a memo once by renaming
it to `.loaded`. Do not rely on a handoff as permanent storage.
