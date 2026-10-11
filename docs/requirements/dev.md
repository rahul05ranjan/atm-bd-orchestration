# dev

Package sources: `atm-bd-orchestration` templates `dev-template`, `dev-fix`, `fix-assignment`, `dev-complete`, `fix-complete`, `task-refused`; `scripts/assignment-gates.py`; `SKILL.md`; `atm-beads/scripts/validate-plan`, `plan_contract.py`.

## Requirements

### All assignments

1. The task id is the bead id; `bd show <bead>` is the assignment; the bead wins over the template and the mismatch is reported.
2. Start a task only after the previous one is closed; after each close read ATM, and only then does the next task start.
3. Work only on the assigned branch and worktree. Never run `gh stack link/sync/rebase/merge` or `bd sync` (the lead is the stack writer and owns sync); `--actor "$ATM_IDENTITY"` on every `bd` write.
4. Before claiming: dev rebases onto `origin/<pr_target>` (a conflict is part of the work); dev-fix and fix only fetch (their branch was cut from the stack top). Then run `assignment-gates.py dev`; `READY`: `bd update <bead> --claim`, then `atm task start <bead> "<one line>"`.
5. `NOT_READY`: do not claim or start; find the blockers; close the task `refused` (`bead_state` open), `reason_md` = bead, cause, who must move and the dependency to add (`bd dep add <bead> --blocked-by <blocker>`). A blocker met mid-task (work another bead or agent owns that is not done; a shared change you can make yourself is a quick fix, 26, and the task continues): return the bead open, unassigned, blocker in notes, close the task `refused` the same way. Never wait.
6. Any other gate code: reuse a matching workflow class bead (append task id, head, command, evidence) and cite it in the refusal; none matches: report the signature to the task assigner and cite that message; never create a per-task bead.
7. Commit as items land and push early; a push or `atm send` closes nothing.
8. Never weaken a test, skip a criterion, or leave a placeholder; run `<test_command>` and `<lint_command>` (dev and dev-fix: also the bead's validation commands) to zero failures, including lint failures inherited from a lower layer.
9. Before closing, the dev rebases onto the stack's current top (`assignment-gates.py stack-top`; any other exit is a code to refuse with, never a guess), re-runs the tests, force-pushes and opens the PR against that top (never draft; a quick-fix finding fixed on a branch that already has its PR keeps that PR), then runs `/sc-gh-stack-view`.
10. Close bead and task together with the close template, `bd close` first and the task only if it succeeded; the close carries the pushed head, the top rebased onto, the PR and the verbatim `/sc-gh-stack-view` output. The task close is the report; it returns to the task assigner.

### Dev (`dev-template`)

11. Read the bead: scope, deliverables, deletion targets, validation, acceptance criteria; `metadata.owned_paths` is the fence; a field the poured dev bead lacks comes from its sprint container.
12. Read every `metadata.requirements` and `metadata.adrs` id; satisfy each; `NONE` but the work touches one: tell the task assigner before coding past it.
13. Read `policy_path` and every guideline it names for the languages touched.
14. Read the complete plan and enumerate all tasks that must be completed in the primary and any child beads; create an itemized private checklist outside the tracked tree with every task identified; work through the checklist one item at a time; then go through it again one item at a time and confirm each is fully met; nothing outside the bead's scope.
15. Close: `bd close --reason "dev complete at <short sha>"` + `dev-complete.md.j2` with the deliverable inventory and gate output; deletion work adds a before/after inventory.
16. Cannot complete: class bead or report (6); `bd update --status blocked --assignee "" --append-notes "DEV_CANNOT_COMPLETE; ..."`; never `bd close` (it would release the sanity check); task `refused`. Unfinished is a failure.

### Dev-fix (`dev-fix`)

17. Not ready while `closed` means never reopened. The fixes go on the assigned new layer cut from the stack's top; the sprint's first layer (`pr_target`) and any fix layer above it are frozen: never rebase, re-target or push them.
18. Read the complete plan and enumerate all tasks that must be completed in the primary and any child beads (`bd list --parent <bead> --status open --json`); create an itemized private checklist outside the tracked tree with every task identified; work through the checklist one item at a time, closing each child when fixed; then go through it again one item at a time and confirm each is done.
19. Re-check the whole bead; re-read its requirements and ADRs.
20. Close and cannot-complete as Dev 15 and 16, the deliverables listing each finding and its fix.

### Fix (`fix-assignment`)

21. Read the bead, its `metadata.sprint_bead`, and the finding's requirements and ADRs; read `policy_path` and its guidelines.
22. Confirm the defect at the cited file:line; absent: no work, close `not_reproducible` with the file:line checked.
23. Fix every in-scope occurrence of the pattern; touch only what the remedy needs.
24. Close: `bd close --reason "fixed at <short sha>"` (or `"not reproducible: <file:line>"`) + `fix-complete.md.j2`, outcome `fixed` (with commit and PR) or `not_reproducible` (none).
25. Cannot fix: class bead or report (6); return the bead open, unassigned, `FIX_CANNOT_COMPLETE` in notes; task `refused`. Never close a finding unfixed.

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
