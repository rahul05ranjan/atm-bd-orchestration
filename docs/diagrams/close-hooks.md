# Task close: close script, sanity hook and close vars

A task closes through one of two package scripts, and both write beads only
through the bd-cache journal (bd-cache.md):

| Role | Close | Run by |
| --- | --- | --- |
| dev, fix | `atm-task-close.py --role dev\|fix --vars <close vars> --result <result>` | the dev |
| sanity | `atm task close <sanity> completed --template sanity-task-close.md.j2 --vars <selected result>`, handled by the ATM `task.close` hook `sanity_task_close_hook.py` | dev-sanity; the hook runs in the daemon |
| qa | `atm-task-close.py --role qa --vars <close vars>`, after the fix groups are poured | quality-mgr |

The sanity hook is registered per phase from the primary checkout
(`atm hook register task.close --phase <x> --cwd . --module ... --custom
'{"task_prefix": ..., "sanity_agents": [...]}'`) and disabled with
`atm hook disable --phase <x> task.close`. It runs under a 2.5 s budget.

## Dev or fix close

```mermaid
flowchart TB
  classDef file fill:#f1f3f4,stroke:#5f6368,stroke-dasharray:4 3,color:#000
  classDef script fill:#e6f4ea,stroke:#188038,color:#000
  classDef atm fill:#fef7e0,stroke:#f9ab00,color:#000
  classDef bad fill:#fce8e6,stroke:#c5221f,color:#000

  VARS[/"lead's close vars file<br/>the role's required_variables_by_role, unchanged"/]:::file
  RES[/"dev's result file<br/>result_variables_by_role only"/]:::file
  MERGE{"a field in both files,<br/>or not a result field?"}
  BADV["VARS.INVALID / VARS.UNKNOWN"]:::bad
  DERIVE["derive commit, pr_number, pr_url, rebased_onto,<br/>generated_at from git and the PR at HEAD;<br/>PR and stack checks"]:::script
  WRITE["journal: record commit, branch, pr_number, pr_url<br/>on the checked bead, then close it<br/>(each write drained at once)"]:::script
  APPLIED{"write applied?"}
  PEND["BEADS.WRITE_PENDING<br/>rerun with the same vars; applied once"]:::bad
  ESCW["BEADS.WRITE_ESCALATED<br/>lead sets the bead, bd_cache.py resolve seq"]:::bad
  REPORT["dev-complete or fix-complete to the lead"]:::atm
  ASSIGN["assign the sanity task to the dev-sanity member,<br/>dev-sanity-template.xml.j2, as the lead"]:::atm
  DONE["close the dev's ATM task"]:::atm

  VARS --> MERGE
  RES --> MERGE
  MERGE -- yes --> BADV
  MERGE -- no --> DERIVE --> WRITE --> APPLIED
  APPLIED -- pending --> PEND
  APPLIED -- escalated --> ESCW
  APPLIED -- yes --> REPORT --> ASSIGN --> DONE
```

The dev never types the derived fields and never adds fields to the lead's
file.

## Sanity close hook

```mermaid
flowchart TB
  classDef script fill:#e6f4ea,stroke:#188038,color:#000
  classDef atm fill:#fef7e0,stroke:#f9ab00,color:#000
  classDef ok fill:#e6f4ea,stroke:#188038,color:#000
  classDef bad fill:#fce8e6,stroke:#c5221f,color:#000

  CLOSE["dev-sanity: atm task close sanity completed<br/>--template sanity-task-close.md.j2 --vars selected result"]:::atm
  ROUTE{"task id has the phase prefix, caller in<br/>sanity_agents, bead labelled stage:dev-sanity?"}
  DEFAULT["pass-through: default ATM close"]:::ok
  READ["read sanity and dev from bd-cache, then one batched<br/>bd list --id refresh; on failure decide on cached rows<br/>and name read_fallback"]:::script
  VALID{"valid? dev_bead and its blocks edge, verdict PASS or FAIL,<br/>findings as deliverable and issue, caller is the sanity<br/>assignee, dev bead assignee is the dev task assignee"}
  ESC["refused, no bead write, task stays open:<br/>escalation task sanity.close-error-id to the phase lead<br/>(hook-close-escalation.xml.j2), one per cause"]:::bad
  VERDICT{"verdict"}
  PW["journal: close sanity, PASS at sha"]:::script
  FW["journal: reopen dev (precondition: dev task assignee);<br/>sanity open and unassigned, blocks edge kept"]:::script
  DRAIN["start bd_cache.py drain detached;<br/>the bd-cache cron drains if it cannot start"]:::script
  PA["ATM: close the dev task if not complete<br/>(sanity-dev-close.md.j2), close the sanity task"]:::atm
  FA["ATM: sanity-failed-template.md.j2 to the dev,<br/>close the sanity task"]:::atm
  STACK["fixed PASS: dev-sanity hands stack input<br/>to sanity-stack, which links the layer"]:::ok

  CLOSE --> ROUTE
  ROUTE -- no --> DEFAULT
  ROUTE -- yes --> READ --> VALID
  VALID -- "no, CANNOT_RUN, or a hook error" --> ESC
  VALID -- yes --> VERDICT
  VERDICT -- PASS --> PW --> DRAIN
  VERDICT -- FAIL --> FW --> DRAIN
  DRAIN -- PASS --> PA --> STACK
  DRAIN -- FAIL --> FA
```

All validation comes before the first journal write. Each write's
idempotency key comes from the close plan, so a retried close journals
nothing twice. The hook returns its ATM actions once the writes are
journaled; a slow or failing bd never holds the task open, and a write the
drain cannot apply is escalated by the bd-cache cron (bd-cache.md). The owner
of a refusal is named in it: sanity for an invalid result, the phase lead for
missing planning data, the hook maintainer for a module defect.

The hook neither dispatches QA nor links the stack. Once the drain has
applied the PASS, the qa bead is in `bd ready`, and the lead builds its vars
with `close-vars.py <qa bead> --qa-assignment` (below) and runs the
`atm task assign` it prints. On FAIL the lead re-assigns the dev's task; the
dev fixes, rebases, pushes and runs `atm-task-close.py` again.

### Legacy sanity close

A phase without the hook registration closes sanity with `sanity-close-vars`
and `atm-task-close.py --role sanity` (assignment `sanity_close_mode` =
`legacy`). PASS closes the sanity bead and task and assigns QA itself. FAIL
increments `metadata.sanity_round`; while it is at most `max_sanity_rounds`
it reopens the checked bead, resets sanity open and unassigned and sends
`sanity-fail`; above the cap it sends `SANITY.ROUND_CAP` to the lead and
reopens nothing.

## Close vars

```mermaid
flowchart TB
  classDef tmpl fill:#e8f0fe,stroke:#3367d6,color:#000
  classDef poured fill:#f3e8fd,stroke:#8430ce,stroke-width:3px,stroke-dasharray:6 3,color:#000
  classDef script fill:#e6f4ea,stroke:#188038,color:#000
  classDef file fill:#f1f3f4,stroke:#5f6368,stroke-dasharray:4 3,color:#000
  classDef atm fill:#fef7e0,stroke:#f9ab00,color:#000
  classDef bad fill:#fce8e6,stroke:#c5221f,color:#000

  POUR["pour: sprint-group, finding-group;<br/>qa-bead and dev-sanity-bead templates<br/>stamp close_fields.POURED"]:::poured
  OLD["open bead poured before the close fields"]:::tmpl
  BF["close_fields_backfill.py --phase x --apply<br/>fills missing fields only, journaled;<br/>a differing field is reported, never overwritten"]:::script
  BEAD["open dev, fix, sanity or qa bead<br/>metadata = CloseFields (validate-plan checks it)"]:::tmpl
  CV["close-vars.py bead --out vars.json<br/>reads one bead from bd-cache; re-reads bd once<br/>when a needed field is missing"]:::script
  NP["CLOSE_VARS.NOT_POURED: run the backfill;<br/>never assemble fields from other beads"]:::bad
  VJ[/"vars.json: the role's required_variables_by_role"/]:::file
  NEXT["next, the manual close: dev or fix writes its result file,<br/>atm-task-close.py --role R --vars --result<br/>(it closes the dev task)"]:::atm
  QV["close-vars.py qa --qa-assignment --out vars.json<br/>poured fields + the checked bead's recorded<br/>commit, branch and PR"]:::script
  QR["ASSIGN_VARS.NOT_RECORDED, CONFLICT,<br/>NOT_PLANNED: never sent with an empty PR"]:::bad
  QA["atm task assign qa_member --template templates.qa"]:::atm

  POUR --> BEAD
  OLD --> BF --> BEAD
  BEAD --> CV
  CV -- "field still missing after the re-read" --> NP
  CV --> VJ --> NEXT
  BEAD --> QV
  QV -- refused --> QR
  QV --> QA
```

Every open task bead carries the fields its close needs from the pour, so a
close reads one bead. A sanity close adds its round (the task's `started`
events) and its QA bead's `reviewers_round1`; a QA close adds its round. The
console's automatic QA dispatch builds its vars with the same resolver,
`close_fields.qa_assignment_vars`.
