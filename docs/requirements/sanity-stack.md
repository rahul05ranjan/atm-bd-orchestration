# sanity-stack (subagent of dev-sanity)

Sources: `agents/sanity-stack.md`, `agents/dev-sanity.md`, the stack bead,
`sc-gh-stack` skill. Rulings: Rand, 2026-10-18, anchored ANC-065..069
(atm-core `docs/plans/phase-bj/anchors.md`).

Dealing with the race is the most important thing this agent does: two devs
cut from the same top both pass sanity, and only one can be the next layer.
The subagent exists for the cases where the three link commands do not go
through; when they always work, dev-sanity runs them itself.

### The stack bead (ledger and lock)

1. One bead per stack, `<prefix>-phase-<x>.stack`, child of the phase epic, created by the lead with the integration branch and closed when the stack lands. Its metadata is the whole stack: `trunk`, `stack_number`, and `layers`, an ordered list of `{branch, pr, head, base, linked}`; the last entry is the top. It is the only record of layer order, and it works with GitHub unreachable.
2. The claim is the lock: `bd update <stack bead> --claim` before any stack write, by sanity-stack and by the lead alike (unstack, restack, land). Release is the compare-and-set `bd update <stack bead> --if-assignee "$ATM_IDENTITY" --status open --assignee ""`. The holder keeps its claim alive with `bd heartbeat` (lease: a script constant, 900 s). An abandoned claim of this stack bead is recovered only with `bd reclaim --id <stack bead> --older-than 1800s`; unscoped `bd reclaim` and event age as expiry are forbidden. After a reclaim: re-derive `layers` from the REST chain (item 9), notice to the lead, proceed.

### Input (fenced JSON from dev-sanity)

3. `checked_bead`, `branch`, `pr_number`, `commit`, `stack_bead`, `budget` (`full` or `lean`), `timeout_s` (default 60). `lean` is set when GraphQL is out or dev-sanity is watching its token budget; GraphQL out is detected and forces `lean`.

### Procedure (one next-top layer per spawn)

4. Claim the stack bead, retrying every 5 s until `timeout_s` (a claim either succeeds or is refused at once, so the wait is only for a link in flight, which takes seconds; a lead restack is the one long hold).
5. Under the lock, re-read and validate `layers` from the bead (never from the input), read the filtered REST pull list, and, when layers exist or a claim was reclaimed, fetch and reconcile the hosted chain. Validate the checked pull and re-read the bead to verify that the claim and ledger are unchanged; then fetch, select the current top (or trunk for an empty ledger), and verify `origin/<top.branch>` is an ancestor of `commit`. Reconciliation of a torn link or reclaim must precede top selection, and the final fetch and ancestry check stay adjacent (lead ruling 2026-10-19 (bj-3 f9)). An already-recorded top is checked as an idempotent replay. `full`: link every `linked: false` layer in order, then this PR as the new top (`gh stack link <stack_number> <pr>`; the first stack from layers 0 and 1 together with `--base <trunk>`), one `/sc-gh-stack-view`; `lean`: verify the PR base ref equals the top branch from the filtered REST pull list (number, base, head, sha, draft; never the raw body) and record `linked: false`; append the layer; write the metadata. The write is the commit point.
6. Release the lock. Return fenced JSON: `stacked` (with the new top and `stack_number`), `rebase_required`, `lock_timeout` or `stack_failed`, with the evidence.
7. Ancestor check fails: `rebase_required`, link nothing, write nothing. A third branch also on the old top is not this spawn's problem; it gets `rebase_required` when its own sanity passes.
8. A link that fails for a stack reason (stale local tracking, a stack number minted anew, a PR retargeted or closed, a non-linear stack): repair with the `sc-gh-stack` recipes within this one layer, never rewriting a layer that has children; unrecoverable: `stack_failed` to the lead as stack owner, lock released, ledger unchanged.
9. `layers` on the bead is the authority; gh-stack's own catalog in the linking worktree (`.git/worktrees/<name>/gh-stack`) is a snapshot. The one repair this agent owns, under the lock and before anything else, is re-deriving `layers` from the linked chain on GitHub (filtered REST pull list bases plus local ancestry) when the bead and the chain disagree (a torn link, an abandoned claim).

### Lock timeout (never blocks the team, never a human)

10. On `timeout_s` the subagent returns `lock_timeout` with the holder, the claim time and the holder's active task (stack bead assignee and `atm task list`).
11. dev-sanity keeps the sanity task active, sends the holder one plain message asking for the release, continues its other sanity checks, and re-spawns after the holder's reply or after another `timeout_s`. The dev keeps waiting on its open task. It never refuses the task, never sends the dev a FAIL, never escalates to a person; when the lease expires, item 2 applies.

### dev-sanity's side

12. One sanity task open: dev-sanity runs items 4 to 7 itself (same order, same lock). More than one open, or its own inline link failed: it spawns sanity-stack, one spawn at a time in verdict order, never two in flight.
13. The sanity close hook runs only after `stacked`; the vars carry the stack result and the hook refuses a PASS close without one. `rebase_required` goes to the dev as `rebase-required.xml.j2` on the dev's open task; the checked bead is reopened to the dev; `sanity_round` does not move; the PASS verdict stays in the ledger.
