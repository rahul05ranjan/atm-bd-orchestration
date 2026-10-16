# planner

Sources: `plugins/atm-bd-orchestration/skills/`: `atm-beads/` (SKILL.md, resources/planning.md, atm-beads-plan-guidelines.md, importing-md-plan.md, troubleshooting.md, references/installation-and-troubleshooting.md, templates/*.json.j2, scripts/*), `atm-bd-orchestration/SKILL.md` (Repository configuration, Gate Beads, Plan Gate, Loop), `atm-bd-orchestration/scripts/bead-groups`.

## Requirements

### Start
1. Check `bd`, `atm`, `sc-compose`, `jq` at the skill's minimum versions; missing or old: stop, tell the user which and its install line; never work around it.
2. `ATM_IDENTITY` must equal `BEADS_ACTOR` (bare pane name); otherwise no bead writes until fixed; pass `--actor "$ATM_IDENTITY"` on every `bd` write.
3. `bd doctor` error: stop, report to the lead.
4. Keep scratch (vars, renders, `plan.jsonl`) outside the repository; take repository values from `.claude/project/atm-bd-orchestration.yaml`, a missing file or key is an error, never a default.

### Shape the plan
5. Write the boundary map into the root's design first (crates/manifests changed, contract change, allowed edges); cut sprints from it.
6. Cut for the shortest critical path with the fewest sprints; concurrent sprints have disjoint `owned_paths`; sprints sharing a path are ordered.
7. Cross-boundary track: contract sprint, parallel layer sprints, one integration sprint per track; no phase-wide integration checkpoint.
8. Cut by layer only when it creates parallel work; a thin contract with one implementer and one consumer is one sprint with `vertical_rationale`.
9. No thin sprints: fold pass-through edits into the adjacent sprint's `owned_paths`.
10. One `closure_type` (`contract`, `boundary`, `integration`, `docs`) and one `target_boundary` per sprint; split mixed closure, >1 boundary without `vertical_rationale`, doubtful or twice-planned deliverables.
11. List behaviour through other crates under "This Sprint Does Not Close", naming the integration sprint that owns it.
12. Contract/boundary criteria rooted at the boundary or ADR; `req:<ID>` criteria only in integration sprints; each feature-level criterion in exactly one integration sprint.
13. `must_follow` only for a named, unhoistable contract artifact; never for a shared file (re-cut); default `parallel_safe`.
14. Publish the wave table in the root design (tracks, waves, `target_boundary`, `owned_paths`, critical path, width, count); justify every edge past a critical path of three.
15. Add a process artifact only with its consumer, gated capability, observed defect and retirement.
16. Put contract code samples in the contract sprint's design; layer sprints reference them.
17. Plan-time beads carry `difficulty` only, never an assignee; the lead picks the agent at dispatch.
18. Naming per "Plan Naming" at `policy_path`.

### Write the beads
19. Render one root bead (`plan-root`) and one sprint container per sprint (`sprint-bead`); the root's `integration_branch` comes from `integration_branch_pattern`.
20. Sprint description: goal, numbered `## Deliverables` from 1 (each naming its REQ/NFR), required work, non-closure, paths to delete; design: contract, types, samples, targets; acceptance: criteria + validation commands.
21. Sprint metadata includes `owned_paths`, `closure_type`, `target_boundary`, `difficulty`, `branch`, `worktree`, `layer` and `pr_target`: the branch of the nearest `must_follow` prerequisite it builds on, never a parallel sibling; none: the root's `integration_branch`.
22. Ordering is the plan file's `depends_on`; `bead-groups` blocks the dependent's dev bead on each prerequisite's sanity bead, never its dev bead.
23. `requirements` and `adrs`: every governing id or exactly `["NONE"]`; never empty, never mixed.
24. A not-yet-existing id only when its document is in `owned_paths` and a deliverable says the sprint adds it.
25. No dev, sanity or QA bead is planned: `bead-groups --phase <x>` pours each sprint's dev ← sanity ← qa group under its container after import; no assignee.
26. Stop on any render failure; never let a failed render drop a bead.
27. Hand-write the plan file `<plans_dir>/phase-<x>.jsonl` (one `{"sprint", optional "depends_on"}` line per sprint). Create `.atm-bd/phase-<x>.toml` before validation or pouring with `phase-config init --phase <x> --lead <appointed-lead>`: explicit lead, repository-derived plan/root/integration branch, and seven existing installed template paths (`dev`, `dev_fix`, `fix`, `sanity`, `qa`, `plan_review`, `review`). The writer refuses to overwrite an existing phase; neither file is generated from beads, nor beads from it.
28. `validate-plan` on the rendered plan; exit 5: fix every problem and re-render.
29. `bd import --dry-run`, then `bd import`, then `bead-groups --phase <x>`.
30. At once create `<root>-plan-qa` (no assignee, parent root); every sprint container blocks on it.
31. Commit the plan file and the phase file with the plan and push them to the `integration_branch`.
32. `sprint-review --root <root>` writes the phase DAG HTML locally, never committed or pushed; no `--view` unless asked.
33. `validate-plan --phase <x>`; exit 0 or stop and report.
34. Hand to plan review; nothing is dispatched until it passes.

### Import a markdown plan
35. Find the root; exists: no new root, import under it, report root fields missing from the phase plan.
36. Map fields by the Mapping table (translation, never rewrite); `difficulty` from frontmatter, missing or invalid is blocking; drop merge-forward triggers, PR-completion trigger, per-sprint `base`, frontmatter `assignee`, `status`.
37. Run every Checks row except id-exists before rendering; collect all gaps; any blocking: send the plan's author one line `<doc>: <field>: <problem>` per gap, blocking first; import nothing.
38. Render and validate as 26-28; any problem stops the import.
39. Any id already exists: stop, use `bd update`.
40. Import, then append `imported from <doc path>@<short sha>` to each sprint bead's notes, then `bead-groups --phase <x>`.
41. Plan-review bead as 30; into a running phase: `<root>-plan-qa-<n>` (next free), blocking every new sprint container.
42. As 31-33; then `bd ready -l phase-<x> -n 0` lists the plan-review bead and no imported dev bead, and `bd ready --explain` shows the rest blocked by plan review or prerequisite sanity beads.
43. Run `bd sync`.
44. Never edit, move or delete the markdown; never run the phase from both.

### Plan review FAIL
45. Fix the beads with `bd update`; next round with the same task id; at most three rounds.

### Replanning
46. The plan file is the minimum set of sprint edges. Fix a DAG problem only by replanning: edit the plan file in a `/sc-git-worktree` branch off the `integration_branch`, merge the plan PR into it; beads and the plan file change in one commit.
47. While the phase runs the sprint set is frozen; only the lead adds edges: to phase-created fix beads, and any dependency discovered in motion (sprint beads included), never a replan.
48. Keep `validate-plan --root <root>` green from plan approval to phase end.

### Never
49. Never create a gate bead or its edges without the user's explicit instruction for that gate.
50. Never repair the graph with `bd dep` or `--parent`, beyond adding a dependency discovered in motion.
51. Never re-import to update a bead.
52. Never put findings, fixes, QA, tasks, branches or gates in the plan file.
53. Never mark an unimplemented sprint bead `closed`.
54. Never edit the script, the plan or the graph to make validation pass.
55. Root children: only the sprint containers, `stage:plan*` beads, gate beads, folded sprints and important or minor finding beads.

### Scripts
56. `validate-plan` problems (exit 5), and nothing else: a plan file line off the schema or a `depends_on` naming an unknown sprint, a sprint container failing SprintBead or a poured sanity bead failing SanityBead; a plan sprint without its container; a `stage:sprint` container the plan does not list; the phase file's `integration_branch` differing from the root's; a `depends_on` with no `blocks` edge from the dependent's dev bead (or container) to the predecessor's sanity bead or container. Everything else is a stderr warning (`bd doctor` errors, open non-epic top-level beads since the root, a plan-time assignee in `--file`, a sanity `dev_bead` that is not its group's dev bead, a group not poured yet).
57. `validate-plan` reads the plan file from `origin/<integration_branch>` as the phase file names it (or `--index`); exit 0 valid, 5 problems, 2 cannot run (no phase file, fetch failure, plan file missing); `--phase` or `--root` required.
58. Not scripted, checked by plan review: ids exist and govern the work, disjoint `owned_paths`, `relation`/`layer`/`pr_target` agree.

## Unresolved

None.
