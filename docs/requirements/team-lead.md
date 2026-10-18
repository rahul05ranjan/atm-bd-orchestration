# team-lead

Sources: `plugins/atm-bd-orchestration/`: `skills/atm-bd-orchestration/{SKILL.md, blocking-findings-guidelines.md, formulas/README.md, references/post-mortem.md, roles/quality-mgr.md, scripts/transitions.py}`, `agents/dev-sanity.md` (lead-facing parts), `skills/atm-beads/{SKILL.md, resources/{orchestrating,planning,importing-md-plan,troubleshooting}.md, references/installation-and-troubleshooting.md, scripts/{validate-plan,resolve-role}}`, `skills/sprint-review/SKILL.md`, `skills/sprint-report/SKILL.md`, `skills/qa-report/SKILL.md`.

## Requirements

### Sequencer

#### Startup
1. Verify `bd`, `atm`, `sc-compose`, `jq`, `gh` + `gh stack` at the skill's minimum versions; missing or old: stop, tell the user the install line; never work around it.
2. Check `ATM_IDENTITY` = `BEADS_ACTOR` (bare identity); mismatch or wrong value: no bead writes until fixed; pass `--actor "$ATM_IDENTITY"` on every `bd` write.
3. Fill every repository value from `.claude/project/atm-bd-orchestration.yaml`; never default one.
4. Resolve each role member with `resolve-role <role>` (exit 2: role unmapped) and put each long-running agent under its role prompt (`roles/quality-mgr.md`, `.claude/agents/dev-sanity.md`).

#### Plan gate
5. `bd doctor` error: report it; never import into or dispatch from that database.
6. Import gaps go to the plan's author; when that is you, supply the branch or status rulings asked for; nothing imports on a blocking gap.
7. Right after import and the pour, create `<root>-plan-qa` with no assignee; every sprint container blocks on it (running phase: `<root>-plan-qa-<n>`, blocking only the new containers).
8. The plan file and the phase file are committed by hand on origin `integration_branch`, never exported from beads; the phase DAG HTML (`sprint-review`) is written locally, never committed or pushed, opened only with `--view`.
9. `validate-plan --phase <x>` exits 0 or stop; then `bd ready` lists the plan-review bead and no dev bead.
10. Dispatch plan review to quality-mgr; no dev bead before it passes.
11. PASS closed: nothing. PASS handed back open with minor findings: fix each with `bd update`, then close `<root>-plan-qa`.
12. FAIL: the author fixes the listed beads; rerun `validate-plan`; dispatch round+1, same task id, `carry_forward` = open finding lines; three rounds max.

#### Dispatch
13. Before the first dispatch, create the root's `integration_branch` from the base branch and push it. The stack bead `<prefix>-phase-<x>.stack` is poured with the phase root (`trunk` = that branch, empty `layers`; `validate-plan` requires it), not created by hand; close it when the stack lands ([sanity-stack.md](sanity-stack.md) 1).
14. Run `bd ready -n 0` after every close, before any other work, and dispatch every ready dev, fix and finding bead; a ready sanity or qa bead is assigned by the close hook as the lead (task-close-hook.md 19) and needs the lead or the console only when `bd ready` lists it with no ATM task; assign a bead only while `bd ready` lists it; order and hold work only with bead edges, gates and `atm task move`, never by telling an agent not to run a task in its queue; never cache it; never dispatch the phase root. The lead may step in at critical points, preferably through a background developer subagent; lead work is never part of the original plan.
15. Route: plan review and QA to quality-mgr; dev to the member you pick for its `difficulty` (a minimum: a dev at that tier, or one tier up when none is idle, e.g. fast to terra/sonnet; never far above it, e.g. fast to a frontier model); sanity to `resolve-role dev-sanity` (mechanical: the dev's close hook does it; the lead only as the fallback in 14); finding to the member you pick; a sanity FAIL is never dispatched: the hook reopens the checked bead on the dev's still-open task and sends `sanity-fail`; a quick-fix finding with `fix-assignment.xml.j2`, no sanity bead, to an idle dev you pick for its `difficulty`, never as an ordinary finding; review to the phase-end reviewer.
16. Important or minor finding: when you assign it, create its sanity bead and its QA bead (QA blocked by sanity, both under the finding's parent) in one `bd import`; a minor finding left in the backlog gets neither until it is assigned.
17. Dev or finding bead: cut its branch and worktree from the current top of its stack (its `pr_target` or a descendant), passed as the assignment's `pr_target`.
18. Set the bead's assignee to the recipient before `atm task assign <agent> --task-id <bead>` with the template; vars from the bead, kept outside the repo; preview with `atm compose`, never render and paste a body.
19. Assignee by priority: frontier dev for blocking and dev, fast agent for important and minor (usual case, not a rule).
20. Dispatch the next sprint once its sanity blockers PASS; never wait on QA.

#### On each close
21. dev-complete, fix-complete `fixed` and sanity PASS need no action: the hook assigned the next bead and sanity linked the layer. The dev's task stays open until sanity PASS; a `task_reminder` for it is expected and is not a stall.
22. `stack_failed` from sanity-stack is yours as stack owner: claim the stack bead, repair in your own worktree, release, then tell dev-sanity to re-run the link; never message the dev for stacking.
23. Sanity FAIL below the cap: nothing to dispatch; the hook has reopened the checked bead to the dev on its open task and sent `sanity-fail`; the fix goes on the same unlinked branch. Overrule a rejection that judges correctness or quality by telling the dev and dev-sanity, never by closing the bead for the dev.
24. Sanity FAIL at the cap (`SANITY.ROUND_CAP`, `max_sanity_rounds` default 5 in the repo config): the dev's task is still open and nothing is reopened; diff flagged files vs last PASS, check the base for foreign commits, then rule (reopen the bead for another round, reassign, or force-close with a reason); no further round without it.
25. `SANITY.PLAN_INVALID`: planning failed for that bead.
26. qa-complete: nothing to pour (quality-mgr poured the fix groups), except a failed quick-fix QA, which pours none: file each blocking finding in its `findings_md` as a finding bead and dispatch it (45); reopen a ceremony closure you disagree with; pick the member for each finding; close the sprint container once nothing under it is open.
27. QA `ROUND_CAP` (FAIL at round 2): no further fix round.
28. fix-complete `not_reproducible`: the same chain as `fixed` without a PR; sanity verifies the absence at the cited line and QA's filing reviewer confirms it; nothing to dispatch. A quick-fix finding, either outcome: once every finding from that failed QA has closed, one more quick-fix QA bead.
29. review-complete: file each finding as a finding bead (`qa_bead` = review bead; provenance from the cited dev bead, the root with the union of real ids when it spans sprints; `found_at_commit` = reviewed commit).
30. task-refused: read reason and bead state; reassign only once `bd ready` lists the bead (a blocker refusal: as 31), split or force-close with a reason; a `blocked` bead is set open with its new assignee before re-dispatch. A cannot-run caused by an announced outage (its workflow class bead open) is not re-dispatched until that bead closes; never force-close meanwhile. `REVIEW_PENDING_JEV`: file the code findings in its notes, re-dispatch only after the Jev outage clears, reusing the prior run IDs.
31. Not-ready or blocker refusal (task `refused`, bead open): fix the named cause (a blocker that is not a bead, such as an unfiled fix, first gets a finding bead or workflow class bead, whichever fits), add the recommended dependency (or the right one) so `bd ready` holds the bead; when bd refuses it (one edge type per bead pair, no parent-child edge), add the edge to an open bead the blocker's work goes through instead, unless it already blocks the bead (a QA bead whose checked bead changes after its sanity PASS: the change is a new fix bead, and its sanity bead holds the QA bead; a passed sanity is never reopened), never removing an edge; re-assign the same task id, template and vars only once `bd ready` lists it; never answer it with "wait"; unwanted work: close the bead with a reason. An assignee silent past the re-nudge: announce it (Lead Role), set the bead open with a new assignee, re-assign the same task id with the same template and vars.
32. After every bead write, `validate-plan --phase <x>`; any problem: stop dispatching, report to the user; never repair the graph (`bd dep`, `--parent`) beyond adding a dependency discovered in motion.
33. The plan file is the minimum set of sprint edges. A DAG problem is fixed only by a replan PR off the root's `integration_branch`, merged into it; in motion the sprint set is frozen and the lead only adds dependencies: to fix beads, and any dependency discovered in motion (sprint beads included), never a replan; a planned edge is never removed.

#### Findings and decisions
34. Check a "blocking" finding's scope (requirement, code, ownership) before its priority; out-of-phase issues go to the owner's backlog with evidence.
35. Read the cited code at the reported commit and current head; separate defect from remedy; correct an overbroad remedy on the finding, keeping its evidence.
36. Reject process-artifact remedies lacking consumer, gate, defect and retirement condition; keep the real defect.
37. Unresolved decision: create a `decision` bead (question, options, contract, affected beads, cost of being wrong, provisional choice) for the user or their delegate; it blocks phase closure, never development.
38. Take conservative reversible provisional choices; hold only work that depends on a significant decision.
39. Never close an unresolved finding or claim PASS to release a queue; silence or elapsed time is not approval.
40. Create a `bd gate` and its edges only on the user's explicit instruction; human gates need recorded user agreement.
41. A serious infrastructure failure is announced once per cause (its workflow class bead) to the ATM escalation recipients, else the task assigner saying none is set; a fallback is used so work moves, never silently: each use is logged with its verbatim error and announced the same way. While an outage class bead is open, re-test its cause each Loop pass and close it on success.

#### Stack
42. Be the stack owner: `unstack`, `sync`, `rebase` and `merge` are yours, `link` is the sanity side's on PASS; every stack write, yours included, starts by claiming the stack bead and ends by releasing it (sanity-stack.md 2), and a `lock_timeout` reported against you means release it or answer the holder request. Rebasing is the dev's, before its close hook runs (the hook checks the current top from the stack bead is an ancestor of the pushed head and nothing more); a layer is linked on sanity PASS, never at dev-complete; the lead fixes only what the dev could not, or another stack problem, before dispatching sanity or QA, in its own worktree, and dispatches with the new `commit`, `base`, `branch`, `pr_number` and `worktree_path`. A layer that has passed QA, or has layers above it, is never rebased, and a rebase never re-dispatches QA or sanity on any other layer.
43. Layers link in sanity-PASS order, recorded in the stack bead's `layers` (the only record of order); `pr_target` never changes after the cut (a sanity FAIL or rebase-required stays on the same branch).
44. Parallel quick fix: pick its `pr_target`, the lowest branch holding what the change needs; the finder (or a background developer subagent when every roster agent is busy) cuts `fix/<thing>` from the stack top as a new layer; link it, then one QA round on a quick-fix QA bead (no sanity check).
45. A failed quick-fix QA: file each blocking finding as a finding bead and dispatch it with `fix-assignment.xml.j2` to an idle roster agent (the finder when idle; when all are busy and its `difficulty` suits the lead, to the lead itself, which runs the template's gate, claim, start and close steps and acts on the subagent's report while a background developer subagent does the fix steps; else it stays in `bd ready` for the first suitable agent to go idle), no sanity bead, on the same branch when nothing is linked above it, else a new layer cut from the stack top; once every finding from that QA has closed, one more quick-fix QA bead.
46. The quick fix lands with the stack; others rebase onto the top at dev-complete; tell each owner the base moved; record the fix branch and PR in the finder's and every touched bead's notes.
47. Merge no PR into the integration branch or a stack layer without QA.
48. Verify branches read-only; never run a state-changing command in an assignee's worktree.

#### Sync
49. `bd sync` after import, after each close before the next `bd ready`, at phase end before landing: 0 continue; 1 report, keep working, retry next close; 2 or 4 stop dispatching, report; 3 retry next close.

#### Phase end
50. When only the root is open, land the stack on the root's `integration_branch`.
51. Create `<root>-review`; dispatch `review-template.xml.j2` with `branch` = `integration_branch`, `commit` = its fetched pinned head.
52. Land review-finding fixes on the integration branch; filing reviewers verify there; no full re-sweep.
53. Route open, regressed or unverified findings from the reconciliation back to their owners.
54. Confirm the report says `integration_review_passed` and its SHA equals the integration head; then close the root, merge to the base branch, `bd sync`.
55. Handover: send the incoming lead open task ids, open PRs and the stack number; announce the new lead; in-flight tasks keep their assigner.

#### Reports
56. Status: `sprint-report`; rows are never hand-typed.
57. QA metrics: `/qa-report`, both QA log tables in the reply, timestamps converted to local.

#### Scripts
58. `validate-plan`: exit 0 valid, 5 problems, 2 cannot run; never edit it, the plan or the graph to pass.
59. `transitions.py` `next_transition`: sanity PASS without QA → create QA bead; QA PASS with only minor open → PASS with backlog; QA FAIL round>=2 → ROUND_CAP; FAIL with blocking/important → one fix round; else wait.

### Swarm-master

None in the package.

## Unresolved

1. Plan Gate order: `SKILL.md`, `planning.md` run `sprint-review` then `validate-plan` vs `importing-md-plan.md` step 9 the reverse.
2. Integration branch timing: `SKILL.md` Dispatch creates it "before the first dispatch" vs Plan Gate, `sprint-review/SKILL.md`, `validate-plan` needing it on origin before plan review.
