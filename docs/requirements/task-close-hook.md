# atm-task-close (close hook)

Sources: `scripts/atm-task-close.py`, `templates/task-close-vars.j2`,
`templates/sanity-fail.xml.j2`, `templates/rebase-required.xml.j2`, the dev, fix, sanity and qa templates; the stack bead ([sanity-stack.md](sanity-stack.md)).
Rulings: Rand, 2026-10-18, recorded verbatim on randlee/atm-core#1873, the
atm-core task-transition hook this script stands in for until the daemon
runs it.

### Contract

1. One script, `atm-task-close.py --role <dev|fix|sanity|qa> --vars <file>`, is the only way a dev, fix, sanity or QA task completes. The agent never runs `bd close` or `atm task close ... completed` by hand for those roles.
2. The vars file is the sc-compose `--vars` payload the role's templates already take. The script validates it by rendering `task-close-vars.j2`, a header-only template whose `required_variables` name every field the role needs, in strict mode: a missing or unknown field is an error before any check runs, and the fields that must be non-empty (`commit`, `pr_number`, `branch`) are guarded the way `fix-complete.md.j2` guards `fixed`. The same file feeds the close report and the next assignment, so nothing is typed twice.
3. The result is JSON on stdout and stderr, `{"ok": {...}}` or `{"err": {"code", "message", "fix"}}`, and the exit code mirrors it. Every `err` names the exact fix; there is no separate ledger.
4. Commands that need the lead's authority (`atm task assign`, closing the dev's task) run with `ATM_IDENTITY=<lead>` and `ATM_TEAM=<team>` set on that one subprocess, `lead` from `.atm-bd/phase-<x>.toml` and the team from `.atm.toml`. Every other `bd` and `atm` write runs as the caller, with `--actor "$ATM_IDENTITY"`.
5. `--skip-checks` skips the PR and stack checks only, never the vars validation or the state writes. It appears only in `--help`: "skips the PR and stack checks. Do NOT use without permission from the phase lead." No template or prose names it.
6. GitHub unreachable or rate limited is not a failure: the PR checks fall back to local git (the pushed head is on `origin/<branch>`, `origin/<current stack top>` is an ancestor of it (or `origin/<pr_target>` when the ledger is empty)), the result records `github: unavailable`, and sanity and QA proceed. The lead owns stack hygiene.

### Role dev and fix (`--role dev`, `--role fix`)

7. Checks, in order: the vars (2); the worktree is clean and HEAD is the pushed head on `origin/<branch>`; the current top from the stack bead's `layers` (its `pr_target` when the ledger is empty) is an ancestor of HEAD, read locally, no GitHub call; an open PR exists for `branch` with HEAD as its head (when GitHub is reachable; its base is retargeted by the link, so the hook does not check it). Nothing else: not the whole stack, not lint or tests (the dev ran the gates before calling; sanity runs lint). The dev rebases onto the current top before every hook run, after a sanity FAIL or a rebase-required too.
8. On pass: `bd update <bead> --set-metadata commit=<sha> pr_number=<n> pr_url=<url>`, then `bd close <bead> --reason "dev complete at <sha>"` (`fixed at <sha>` for a fix); immediately `atm task assign <sanity member> --task-id <sanity bead> --template dev-sanity-template.xml.j2 --vars <same file>` as the lead (4), with no intervening command or report; then the dev-complete or fix-complete report to the lead by plain `atm send --template` (not a task close). The dev's ATM task stays open.
9. The sanity bead is the group's: `metadata.sanity_bead` on the checked bead when present, else the open `stage:dev-sanity` bead whose `metadata.dev_bead` is the checked bead. None: `err` naming the lead, who creates it (team-lead 16).
10. `not_reproducible` (fix only): the PR and head checks are skipped (nothing is pushed); the close reason is `not reproducible: <file:line>`; the hook assigns sanity before the report as in item 8, using a derived vars file with branch and commit set to the current stack top. Sanity verifies the one deliverable, the absence at the cited line; QA's filing reviewer confirms it. No special close, no special assignment.
11. Refused and cancelled closes do not go through the hook: `atm task close <id> refused --template task-refused.md.j2` as before (dev 5 and 16).
12. After a pass the dev waits for the verdict: a `task_reminder` during the wait is expected and answered by waiting; the dev starts nothing else until its task closes (PASS), the sanity-fail message arrives (FAIL) or the rebase-required message arrives (another layer took the top first: rebase onto the new top, push, run the hook again; `sanity_round` does not move).

### Role sanity (`--role sanity`)

13. Input: the verdict (`PASS`, `FAIL`, `CANNOT_RUN`), the sha, the selected undone deliverables with their reasons, the checked bead and its assignee, the group's QA bead, and for PASS the stack result from sanity-stack (`stacked`, new top, `stack_number`; [sanity-stack.md](sanity-stack.md) 13). A PASS close without a stack result is refused, except for a `not_reproducible` fix: the layer is linked before the close, under the stack bead lock, by dev-sanity itself or by the sanity-stack subagent.
14. PASS: `bd close <sanity bead> --reason "PASS at <sha>"`; `atm task close <checked bead> completed` as the lead (4), the dev-complete report having been sent by the dev's hook; `atm task close <sanity bead> completed --template dev-sanity-complete.md.j2`; `atm task assign <qa member> --task-id <qa bead> --template qa-template.xml.j2 --vars <file>` as the lead, with `base` = the PR base, `commit`, `round`, and for a fix bead `carry_forward` = the fix bead.
15. FAIL below the cap: `bd update <sanity bead> --set-metadata sanity_round=<n+1>` (`n` the current value, default 0); `bd update <checked bead> --status open --assignee <its assignee>`; `atm send <assignee> --template sanity-fail.xml.j2 --vars <file>`: the sprint and checked bead ids and title, branch, PR and sha checked, round n of `max_sanity_rounds`, and one numbered line per deliverable that was NOT complete with the selected reviewer's reason verbatim, nothing else; `atm task close <sanity bead> completed --template dev-sanity-complete.md.j2` (verdict FAIL). The FAIL is handled entirely by ATM: no finding children, the message and the sanity ledger are the record. The sanity bead is re-blocked by its `blocks` edge and is ready again when the dev's hook closes the checked bead; the console or the lead assigns it again (same task id, reopened in place).
16. FAIL exceeding the cap: `max_sanity_rounds` from the repo config (`.claude/project/atm-bd-orchestration.yaml`, rendered from `registry.yaml`), default 5. The hook reopens nothing: it sends the lead `SANITY.ROUND_CAP` with the undone deliverable numbers and closes the sanity task (FAIL). The dev's task stays open until the lead rules.
17. CANNOT_RUN: as today (dev-sanity 31): bead open, no assignee, task `refused`; the dev's task stays open.

### Role qa (`--role qa`)

18. Input: the verdict, the sha, the round, the pours made. The hook closes the QA bead and task together (`bd close` first) with `qa-complete.md.j2` and sends the lead a status message, PASS or FAIL alike. It assigns nothing: quality-mgr has already poured the fix groups, and fix dispatch is the lead's judgment.

### Lead and console

19. Sanity and QA assignment are mechanical and belong to the hook. The fallback for a sanity or QA bead that `bd ready` lists with no ATM task (a hook that died between its commands) is the console or the lead assigning it as the lead; the console checks `atm task list` before assigning, and a second assign of the same id is an in-place update, never a second task.
20. The lead does not link layers: the sanity side does, under the stack bead lock, on PASS ([sanity-stack.md](sanity-stack.md)). The lead creates the stack bead with the integration branch, claims it before its own unstack, restack and land, and owns stack hygiene and every `stack_failed`. A sanity FAIL or rebase-required is fixed on the same unlinked branch.
21. Dev and fix assignment stay with the lead: the dev starts its next queued task when the hook closes its task on sanity PASS; fix beads from QA go to the dev the lead picks at the finding's difficulty.
