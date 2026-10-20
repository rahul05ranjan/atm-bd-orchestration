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
2. The claim is the lock: `bd update <stack bead> --claim` before any stack write, by sanity-stack and by the lead alike (unstack, restack, land). Release is the compare-and-set `bd update <stack bead> --if-assignee "$ATM_IDENTITY" --status open --assignee ""`. The holder keeps its claim alive with `bd heartbeat` (lease: a script constant, 900 s). A refused claim returns `lock_timeout` immediately; recovery of a stale claim belongs to the phase lead.

### Input (fenced JSON from dev-sanity)

3. `checked_bead`, `branch`, `pr_number`, `commit`, `stack_bead` and `budget` (`full` or `lean`) are carried in the dev-sanity assignment's close context. The close hook copies explicit values and supplies `full` only when an override is absent. GraphQL out is detected and forces `lean`.

### Procedure (one next-top layer per spawn)

4. Make one claim attempt on the stack bead. If another agent holds it, return `lock_timeout` immediately; do not poll or wait.
5. Under the lock, re-read and validate `layers` from the bead (never from the input), read the filtered REST pull list, and, when layers exist, fetch and reconcile the hosted chain. Validate the checked pull and re-read the bead to verify that the claim and ledger are unchanged; then fetch, select the current top (or trunk for an empty ledger), and verify `origin/<top.branch>` is an ancestor of `commit`. Reconciliation of a torn link must precede top selection, and the final fetch and ancestry check stay adjacent (lead ruling 2026-10-19 (bj-3 f9)). An already-recorded linked layer is checked as an idempotent replay. `full`: link every `linked: false` layer in order, then this PR as the new top (`gh stack link <stack_number> <pr>`; the first stack from layers 0 and 1 together with `--base <trunk>`), one `/sc-gh-stack-view`; `lean`: verify the PR base ref equals the top branch from the filtered REST pull list (number, base, head, sha, draft; never the raw body) and record `linked: false`; append the layer; write the metadata. The write is the commit point.
6. Release the lock. Return fenced JSON: `stacked` (with the new top and `stack_number`), `rebase_required`, `lock_timeout` or `stack_failed`, with the evidence.
7. Ancestor check fails: `rebase_required`, link nothing, write nothing. A third branch also on the old top is not this spawn's problem; it gets `rebase_required` when its own sanity passes.
8. A link that fails for a stack reason (stale local tracking, a stack number minted anew, a PR retargeted or closed, a non-linear stack): repair with the `sc-gh-stack` recipes within this one layer, never rewriting a layer that has children; unrecoverable: `stack_failed` to the lead as stack owner, lock released, ledger unchanged.
9. `layers` on the bead is the authority; gh-stack's own catalog in the linking worktree (`.git/worktrees/<name>/gh-stack`) is a snapshot. The repair this agent owns, under the lock and before anything else, is re-deriving `layers` from the linked chain on GitHub (filtered REST pull list bases plus local ancestry) when the bead and the chain disagree (a torn link).

### Lock timeout (never blocks the team, never a human)

10. If the one claim attempt is refused, return `lock_timeout` immediately with the stack bead holder and claim time; do not query ATM tasks.
11. A lock_timeout is reported mechanically to the phase lead as stack owner. The sanity task has already closed; it is never held active, refused or changed to FAIL for a stack outcome. The lead owns any subsequent retry or lease recovery under item 2.

### dev-sanity's side

12. After its selected PASS closes, dev-sanity hands the generated stack input to sanity-stack. Handoffs are serialized one at a time, independently of sanity close order; an inline sanity-stack-link invocation follows the same locking procedure.
13. The sanity close hook runs on the selected verdict before linking and accepts an absent stack result. An already-linked layer at the same pinned commit returns stacked, even below the top. rebase_required, lock_timeout and stack_failed notify the phase lead and leave the sanity task closed; none increments sanity_round. The lead owns further dev redispatch or stack repairs.
