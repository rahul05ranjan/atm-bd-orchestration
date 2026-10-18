# quality-mgr

Sources: `skills/atm-bd-orchestration/`: `roles/quality-mgr.md`, `SKILL.md`, templates `qa-template`, `plan-review-template`, `review-template`, `*-assignment.json.j2`, `finding-bead`, `qa-complete`, `review-complete`, `formulas/README.md`, scripts `fix-round-scope`, `check-review-completion.py`, `assignment-gates.py qa`.

## Requirements

### All tasks

1. Stay long-running; the role wins over the repo's `.claude/agents/quality-mgr.md` where they differ.
2. Treat every open task assigned to you as live and run them all at once, each with its own background reviewers: one active ATM task, the others claimed, worked and closed without starting; close each when its verdict is ready, in any order. The task close is the report; it returns to the task assigner.
3. Cannot run: reuse a workflow class bead for the failure signature (append task, head, command, evidence), else report the signature to the task assigner; no per-task bead; return the bead open, unassigned, and close the task `refused`.

### QA round (`qa-template`)

4. Before claim: `bd ready` lists the QA bead, else do not claim or start; close the task `refused` (`bead_state` open) naming the blocker and the dependency to add (`bd dep add <bead> --blocked-by <blocker>`); never wait. A blocker met mid-round: return the bead open and close the task `refused` the same way. PR base = `metadata.pr_target` or a descendant of it, PR head starts with the checked bead's sanity PASS sha (skipped for a quick-fix QA bead, which has no sanity check), worktree HEAD = PR head; else refuse `SANITY_STALE`. Then claim and `atm task start`.
5. Reject an assignment not rendered from the template; read `policy_path`.
6. Review against the checked bead (and `sprint_bead` if set) with its governing requirements and ADRs as `sprint_doc`; extract every deliverable, criterion, deletion, validation item and artifact; completion `X/Y (Z%)`.
7. The change is `git diff origin/<base>...<commit>`, or with `layer_prs` each layer PR's own base...head range (never a diff against a moving base); read content at `<commit>`.
8. Round 1: run `reviewers_round1` plus conditional reviewers `policy_path` requires. Fix round: only each carried finding's filing reviewer (`fix-round-scope owned`, confirmed by `check`), each locked to its own `finding_ref` ids, its result reduced with `filter`.
9. Spawn every reviewer in the background; never run tests, linters or broad analysis in the foreground.
10. Re-verify every cited file:line at `<commit>`; stale or missing evidence is a finding (fix round: fails that carried finding).
11. Round 1: sweep the whole commit for every repeatable pattern.
12. Round 1: screen every finding with `ceremony-finding-screen` (background); normalize severity (`critical`/`Blocking`/`BLOCKING` → blocking, `Important` → important, `Minor`/`low` → minor).
13. Round 1: pour each blocking finding the screen keeps as a fix ← sanity ← qa group under the sprint (quick-fix QA: pour nothing; list the blocking findings in the close's `findings_md` for the lead to file); file every other finding as one finding bead under the phase or feature bead, copying `requirements`, `adrs`, `sprint_bead` and `difficulty` from the checked bead, never defaulting `difficulty`; `blocked_by` only to another finding of this round. Never skip a finding whose render fails: fix its vars.
14. Apply the screen: `keep`/`not_applicable` open; `concern_valid_remedy_ceremony` open with remedy rewritten to the named mechanism; `ceremony` filed then closed with `ceremony: <reason>`.
15. Verify the import added only poured fix groups, finding beads and inter-finding edges; never add a finding edge to a planned sprint.
16. Fix round: file nothing; note each confirmed fix; for each regressed or open carried fix bead pour round n+1; never reopen.
17. Never assign findings.
18. Verdict: only-minor is PASS; blocking or important is FAIL with one fix round; a second FAIL is `ROUND_CAP`, stop dispatch, record root cause.
19. With a PR: post `qa-complete.md.j2` as a PR comment and check stack and CI state (never `--watch`). No PR: say so in notes.
20. Close through the close hook whatever the verdict: `atm-task-close.py --role qa --vars <file>` ([task-close-hook.md](task-close-hook.md)); it closes the bead first and the task only if that succeeded, sends the lead a status message PASS or FAIL alike, and assigns nothing (the fix groups were poured in 16; fix dispatch is the lead's). The QA task itself is assigned by the sanity close hook as the lead. A QA task assigned with `outcome: not_reproducible` is the filing reviewer only: confirm the defect is absent at the cited line and close the QA bead, or pour a fix round.
21. After the closes (never on refusal), append one row each to the phase QA log and stats log, computed fresh; never hand-edit; correct with a workflow-issue bead plus a correcting row; never commit the logs.

### Plan review (`plan-review-template`)

22. Check readiness, claim, start; blocked: close the task `refused` (`bead_state` open) naming the blocker and the dependency to add; a blocker met mid-task: return the bead open, unassigned, blocker in notes, close the task `refused` the same way; never wait.
23. Run `validate-plan --root <root>`: exit 5 every line blocking; exit 2 cannot-run (`PLAN_REVIEW_CANNOT_RUN`); a missing plan file and `bd doctor` errors are blocking.
24. Pin reviewers to `integration_branch` at `origin/<integration_branch>`.
25. Round 1: missing/empty/mixed, unknown (outside New Ids), non-governing or unlisted touched requirement/ADR ids are blocking; never downgraded or screened out.
26. Round 1: run `req-qa` and `arch-qa` per sprint container, `plan-scope-reviewer` in full over all of them (report its `parallelism`), plus the other plan reviewers; screen findings except validate-plan and REQ/ADR ones, list each drop with reason.
27. Fix round: only filing reviewers, locked; `validate-plan` still runs; no screen, no new findings.
28. Report findings one line each: `<bead> <severity> <reviewer> <field>: ...`; file no finding beads.
29. PASS (no blocking/important open): no findings → close bead; minor → assign bead to the task assigner with notes, leave open. FAIL → bead open, unassigned, notes. Fix-round PASS: every carried finding fixed and validate-plan clean. Either way close the task with `plan-review-complete.md.j2`.

### Phase-end review (`review-template`)

30. Check readiness, claim, start; not ready or a blocker met mid-task: refuse as in 22.
31. Require branch = root `integration_branch`, worktree HEAD = `<commit>`; read at `<commit>`; plan = the phase feature bead and its children.
32. JEV post-mortem per `post-mortem.md`: inventory every phase finding (closed, nested); investigate every flagged result; dedupe; report them to the task assigner, who files them as finding beads; append raw evaluations to the phase JSONL.
33. Record `post_mortem_jev`; model error is not PASS; no code findings: `not_applicable`; JEV unavailable: `unavailable`, review stays pending: bead returned open, task refused `REVIEW_PENDING_JEV` with the code findings and `post_mortem_jev` in the refusal notes, which the lead files; never a completion.
34. Make no code changes.
35. Re-verify every file:line at `<commit>`; report SUMMARY, FINDINGS, EXTRACTION-READINESS, RECOMMENDED-NEXT-SPRINTS, INTEGRATION POST-MORTEM.
36. Write to no bead except claiming, closing or returning the review bead. `check-review-completion.py` must exit 0 before closing bead and task with `review-complete.md.j2`.

### Subagents

37. Reviewers: background; pinned `branch`, `commit`, `worktree_path`, `sprint_doc` where the template takes it; never run `bd` or write ATM.
38. Each reviewer gets its assignment rendered from its assignment template as fenced JSON; no free-form prompts.
39. In a fix round, a reviewer's carried ids are its own `finding_ref` ids; with ids, `req-qa`, `arch-qa`, `flaky-test-qa` and `ruthless-boundary-qa` carry the scope lock (a disposition for each id, no new findings).
40. `ceremony-finding-screen` returns `keep`, `not_applicable`, `concern_valid_remedy_ceremony` or `ceremony` per finding.

### Scripts

41. `fix-round-scope owned|check|filter`: filing-reviewer map; check exit 5 `FIX_ROUND_DISPATCH_MISMATCH`; filter keeps only dispositions on own ids, unreported = `open`.
42. `check-review-completion.py`: verdict PASS/FAIL agrees with `integration_review`; PASS needs zero blocking, important, unresolved; `integration_commit` = `commit`; post-mortem counts sum to total; `post_mortem_jev.status` is `completed` or `not_applicable`; `post_mortem_md` non-empty.
43. `assignment-gates.py qa`: `READY`, `PR_REQUIRED`, `PR_TARGET_MISMATCH`, `SANITY_STALE`, `QA_HEAD_MISMATCH`, `GATE_CANNOT_RUN`; exit 0/5/2.

## Unresolved

1. QA refusal codes: `qa-template.xml.j2` step a refuses every mismatch as `SANITY_STALE`; `assignment-gates.py qa` returns `PR_REQUIRED`/`PR_TARGET_MISMATCH`/`QA_HEAD_MISMATCH`, and no template invokes the `qa` subcommand.
2. Phase-end reviewer: `roles/quality-mgr.md`/`SKILL.md` say quality-mgr owns it; `SKILL.md` assigns `<reviewer>` and `review-template.xml.j2` is a single read-only reviewer with no reviewer set.
3. `qa-template.xml.j2` and `plan-review-template.xml.j2` pass `qa_round` to every reviewer; only `ruthless-boundary-qa-assignment.json.j2` declares it (`plan-scope-reviewer` takes `round_index`).
