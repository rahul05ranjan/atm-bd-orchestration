# dev

Package sources: `atm-bd-orchestration` templates `dev-template`, `fix-assignment`, `sanity-fail`, `rebase-required`, `dev-complete`, `fix-complete`, `task-refused`; `scripts/assignment-gates.py`, `scripts/atm-task-close.py`; `SKILL.md`; `atm-beads/scripts/validate-plan`, `plan_contract.py`.

## Requirements

### All assignments

1. The task id is the bead id; `bd show <bead>` is the assignment; the bead wins over the template and the mismatch is reported.
2. Start a task only after the previous one is closed. A dev or fix task closes on sanity PASS, by the sanity close hook as the lead, not when the dev pushes: after running the close hook the dev waits for that close, for a `sanity-fail` message, or for a `rebase-required` message (a `task_reminder` during the wait is expected and answered by waiting). After each close read ATM, and only then does the next task start.
3. Work only on the assigned branch and worktree. Never run `gh stack link/sync/rebase/merge` or `bd sync` (the lead is the stack writer and owns sync); `--actor "$ATM_IDENTITY"` on every `bd` write.
4. Before claiming: dev rebases onto `origin/<pr_target>` (a conflict is part of the work); fix only fetches (its branch was cut from the stack top); a sanity-fail or rebase-required round stays on its branch and rebases onto the current top (the last entry of the stack bead's `layers`) before the hook runs again. Then run `assignment-gates.py dev`; `READY`: `bd update <bead> --claim`, then `atm task start <bead> "<one line>"`.
5. `NOT_READY`: do not claim or start; find the blockers; close the task `refused` (`bead_state` open), `reason_md` = bead, cause, who must move and the dependency to add (`bd dep add <bead> --blocked-by <blocker>`). A blocker met mid-task (work another bead or agent owns that is not done; a shared change you can make yourself is a quick fix, 26, and the task continues): return the bead open, unassigned, blocker in notes, close the task `refused` the same way. Never wait.
6. Any other gate code: refuse with the code, command and output in `reason_md`; no class bead, no per-task bead.
7. Commit as items land and push early; a push or `atm send` closes nothing.
8. Never weaken a test, skip a criterion, or leave a placeholder; run `<test_command>` and `<lint_command>` (dev, and every sanity-fail round: also the bead's validation commands) to zero failures, including lint failures inherited from a lower layer.
9. Before closing, the dev rebases onto the stack's current top (the last entry of the stack bead's `layers`, else `pr_target`; read locally, no gh call), re-runs the tests, force-pushes and opens the PR against that top (never draft; a quick-fix finding fixed on a branch that already has its PR keeps that PR), then runs `/sc-gh-stack-view` when GraphQL is available; its output is informational, never a required var.
10. Complete through the close hook only: `atm-task-close.py --role dev|fix --vars <file>` with the sc-compose vars (pushed head, the top rebased onto, the PR, the deliverable inventory). The hook validates the vars, checks the PR and the pushed head, writes `commit`, `pr_number`, `pr_url` to the bead, runs `bd close`, sends the report to the lead and assigns sanity as the lead ([task-close-hook.md](task-close-hook.md)). An `err` names the fix: fix it and run the hook again, or escalate to the lead. Never `bd close` or `atm task close ... completed` by hand; `--skip-checks` only with the lead's permission.

### Dev (`dev-template`)

11. Read the bead: scope, deliverables, deletion targets, validation, acceptance criteria; `metadata.owned_paths` is the fence; a field the poured dev bead lacks comes from its sprint container.
12. Read every `metadata.requirements` and `metadata.adrs` id; satisfy each; `NONE` but the work touches one: tell the task assigner before coding past it.
13. Read `policy_path` and every guideline it names for the languages touched.
14. Read the complete plan and enumerate all tasks that must be completed in the primary and any child beads; create an itemized private checklist outside the tracked tree with every task identified; work through the checklist one item at a time; then go through it again one item at a time and confirm each is fully met; nothing outside the bead's scope.
15. Close: the close hook (10); it closes the bead with `dev complete at <short sha>` and sends `dev-complete.md.j2` with the deliverable inventory and gate output; deletion work adds a before/after inventory.
16. Cannot complete: refuse as in 6; `bd update --status blocked --assignee "" --append-notes "DEV_CANNOT_COMPLETE; ..."`; never `bd close` (it would release the sanity check); task `refused`. Unfinished is a failure.

### Sanity FAIL (`sanity-fail`) and rebase-required

17. A sanity FAIL arrives as a `sanity-fail` message on the still-open task, listing exactly what was NOT complete and referencing the sprint: the bead is reopened to the dev, `metadata.sanity_round` on the sanity bead counts the rounds. No new task, no new layer: the branch is not yet linked on the stack (sanity links it on PASS), so the fixes go on the same branch and PR. A `rebase-required` message means another layer took the top first: rebase onto the named top, push, run the hook again; it is not a round.
18. Enumerate every undone deliverable the message names with its reason; add each to the private checklist; work through them one at a time; then confirm each is done.
19. Re-check the whole bead; re-read its requirements and ADRs.
20. Rebase onto the current top, then run the close hook again (10) with the new pushed head; the hook re-assigns the same sanity task. At `max_sanity_rounds` (default 5) the hook escalates to the lead instead and the task stays open until the lead rules.

### Fix (`fix-assignment`)

21. Read the bead, its `metadata.sprint_bead`, and the finding's requirements and ADRs; read `policy_path` and its guidelines.
22. Confirm the defect at the cited file:line; absent: no work, close `not_reproducible` with the file:line checked.
23. Fix every in-scope occurrence of the pattern; touch only what the remedy needs.
24. Close: the close hook (10) with outcome `fixed` (commit and PR; closes the bead `fixed at <short sha>`, sends `fix-complete.md.j2`, assigns sanity) or `not_reproducible` (`not reproducible: <file:line>`; the PR checks are skipped since nothing is pushed, everything else is the same chain: sanity checks the absence at the cited line, QA's filing reviewer confirms it).
25. Cannot fix: refuse as in 6; return the bead open, unassigned, `FIX_CANNOT_COMPLETE` in notes; task `refused`. Never close a finding unfixed.

### Parallel quick fix (finder)

26. A change other live branches need never goes in your layer: stop and tell the task assigner the change and the branches it breaks.
27. The lead picks the quick fix's `pr_target` (a lower bound); the finder cuts `fix/<thing>` from the stack's current top (`stack-top --pr-target <pr_target>`) in its own worktree: only the change, compiler-forced implementors/call sites, one test for a bug; test passes; push; PR against that top as a new layer, never into a lower layer or the integration branch; report the PR by plain `atm send`.
28. Sprint task stays open; continue on the rebased layer.

### Scripts

29. `assignment-gates.py dev` prints one code; exit 0 `READY`, 2 `GATE_CANNOT_RUN`, 5 otherwise. `stack-top --pr-target <b>` prints, exit 0, the head of the last open PR of the one open stack based on or containing `<b>` (its base when none is open); with no stack, the top of the one chain of open unlinked PRs based on `<b>`, else `<b>`; several stacks or a branching chain: `STACK_AMBIGUOUS` (5); `GATE_CANNOT_RUN` (2).
30. Checks in order: `validate-plan` (`PLAN_INVALID`), in `bd ready` (`NOT_READY`), open and unassigned or self (`UNCLAIMABLE`), `pr_target` (the sprint container's for a poured dev bead) is `--pr-target` or its ancestor (`PR_TARGET_MISMATCH`), `origin/<pr_target>` ancestor of HEAD (`WRONG_BASE`).
31. Difficulty models (substring): hard fable/opus/astra; normal terra/opus/sonnet; fast luna/haiku.

## Unresolved

None.
