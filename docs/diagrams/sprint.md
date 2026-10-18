# Sprint level

The sprint bead is a container. Its own group is three sibling children,
`dev ← sanity ← qa`: sanity blocks on dev, qa blocks on sanity. Each blocking
finding pours the same shape, `fix ← sanity ← qa`, as siblings directly under
the sprint; there is no finding container. The fix bead is the dev's task and
carries the finding's metadata. bd refuses to close the sprint while any child
is open.

| Bead | Closed by | When |
| --- | --- | --- |
| dev, fix | the close hook (`atm-task-close.py --role dev|fix`) run by the dev | the PR is open on `pr_target` and the pushed head is its head; the hook writes `commit`, `pr_number`, `pr_url` to the bead, closes it, reports to the lead and assigns sanity as the lead. The dev's ATM task stays open until sanity passes |
| sanity | the close hook (`--role sanity`) run by dev-sanity | PASS only, after the layer is linked under the stack bead lock (a head that no longer descends from the top gets `rebase-required` instead, no round counted): the hook closes the dev's task and sanity's, and assigns qa as the lead. On FAIL it bumps `sanity_round`, reopens the dev or fix bead to its assignee, sends the dev `sanity-fail` and leaves sanity open; the `blocks` edge re-blocks sanity. At `max_sanity_rounds` (default 5) it escalates to the lead instead |
| qa | the close hook (`--role qa`) run by quality-mgr | after it has poured a fix group for each blocking finding and created the important and minor finding beads |
| sprint | the team lead | every blocking fix group is closed |

## The sprint group

```mermaid
flowchart TB
  classDef tmpl fill:#e8f0fe,stroke:#3367d6,color:#000
  classDef poured fill:#f3e8fd,stroke:#8430ce,stroke-width:3px,stroke-dasharray:6 3,color:#000

  ROOT["p-phase-x (phase root)"]:::tmpl
  SPR["p-x-2 sprint container<br/>model: SprintBead<br/>deliverables, acceptance_criteria, requirements, adrs,<br/>worktree, branch, pr_target, difficulty"]:::tmpl

  subgraph POUR["poured: sprint-group onto p-x-2"]
    DEV["p-x-2.group-dev<br/>difficulty; closed by the dev"]:::poured
    SAN["p-x-2.group-sanity: gate<br/>model: SanityBead, metadata.dev_bead"]:::poured
    QA["p-x-2.group-qa<br/>metadata: checked_bead, round = 1"]:::poured
  end

  SPR -->|parent-child| ROOT
  DEV ==>|parent-child| SPR
  SAN ==>|parent-child| SPR
  QA ==>|parent-child| SPR
  SAN ==>|blocks| DEV
  QA ==>|blocks| SAN
  QA -.->|validates| DEV
```

Sanity to dev carries `blocks`, so reopening dev re-blocks sanity. QA to dev is
a different pair from QA to sanity, so QA also `validates` the bead it checks.

## QA FAIL: one fix group per blocking finding

```mermaid
flowchart TB
  classDef tmpl fill:#e8f0fe,stroke:#3367d6,color:#000
  classDef poured fill:#f3e8fd,stroke:#8430ce,stroke-width:3px,stroke-dasharray:6 3,color:#000
  classDef closed fill:#e0e0e0,stroke:#757575,color:#000

  SPR["p-x-2 sprint container<br/>cannot close while a child is open"]:::tmpl
  DEV["p-x-2.group-dev (closed)"]:::closed
  SAN["p-x-2.group-sanity (closed, PASS)"]:::closed
  QA["p-x-2.group-qa (FAIL)<br/>closed by quality-mgr after the pours"]:::closed

  subgraph POUR3["poured by quality-mgr: finding-group, finding qa1-f3"]
    FX3["p-x-2.qa1-f3-r1-fix: the dev's task<br/>finding metadata: finding_ref, severity,<br/>reviewer, remedy, filed_by, round"]:::poured
    FS3["p-x-2.qa1-f3-r1-sanity: gate"]:::poured
    FQ3["p-x-2.qa1-f3-r1-qa<br/>verified by the filing reviewer only"]:::poured
  end
  subgraph POUR7["poured by quality-mgr: finding-group, finding qa1-f7"]
    FX7["p-x-2.qa1-f7-r1-fix"]:::poured
    FS7["p-x-2.qa1-f7-r1-sanity"]:::poured
    FQ7["p-x-2.qa1-f7-r1-qa"]:::poured
  end

  DEV ==>|parent-child| SPR
  SAN ==>|parent-child| SPR
  QA ==>|parent-child| SPR
  FX3 ==>|parent-child| SPR
  FS3 ==>|parent-child| SPR
  FQ3 ==>|parent-child| SPR
  FX7 ==>|parent-child| SPR
  FS7 ==>|parent-child| SPR
  FQ7 ==>|parent-child| SPR
  FS3 ==>|blocks| FX3
  FQ3 ==>|blocks| FS3
  FQ3 -.->|validates| FX3
  FX3 -.->|discovered-from| QA
  FS7 ==>|blocks| FX7
  FQ7 ==>|blocks| FS7
  FQ7 -.->|validates| FX7
  FX7 -.->|discovered-from| QA
```

Groups share no edges: one finding, one fix. Fix verification is done only by
the finding's filing reviewer, locked to that finding. If two fixes must land
in order, a `blocks` edge between the two fix beads says so.

## A second fix round: a new group, never a reopen

```mermaid
flowchart TB
  classDef tmpl fill:#e8f0fe,stroke:#3367d6,color:#000
  classDef poured fill:#f3e8fd,stroke:#8430ce,stroke-width:3px,stroke-dasharray:6 3,color:#000
  classDef closed fill:#e0e0e0,stroke:#757575,color:#000

  SPR["p-x-2 sprint container"]:::tmpl
  subgraph R1["qa1-f3 round 1"]
    FX1["p-x-2.qa1-f3-r1-fix (closed)"]:::closed
    FS1["p-x-2.qa1-f3-r1-sanity (closed, PASS)"]:::closed
    FQ1["p-x-2.qa1-f3-r1-qa (FAIL)<br/>closed after the r2 pour"]:::closed
  end
  subgraph R2["poured by quality-mgr: qa1-f3 round 2"]
    FX2["p-x-2.qa1-f3-r2-fix"]:::poured
    FS2["p-x-2.qa1-f3-r2-sanity"]:::poured
    FQ2["p-x-2.qa1-f3-r2-qa"]:::poured
  end

  FX1 ==>|parent-child| SPR
  FS1 ==>|parent-child| SPR
  FQ1 ==>|parent-child| SPR
  FS1 ==>|blocks| FX1
  FQ1 ==>|blocks| FS1
  FX2 ==>|parent-child| SPR
  FS2 ==>|parent-child| SPR
  FQ2 ==>|parent-child| SPR
  FS2 ==>|blocks| FX2
  FQ2 ==>|blocks| FS2
  FX2 -.->|discovered-from| FQ1
```

A failed fix qa is handled like any failed qa: quality-mgr pours the next
round's group with `round = n+1`, then closes the failed qa. No earlier round
is reopened.

## Sanity FAIL

```mermaid
flowchart TB
  classDef tmpl fill:#e8f0fe,stroke:#3367d6,color:#000
  classDef poured fill:#f3e8fd,stroke:#8430ce,stroke-width:3px,stroke-dasharray:6 3,color:#000
  classDef script fill:#e6f4ea,stroke:#188038,color:#000

  SPR["p-x-2 sprint container<br/>cannot close: dev is open"]:::tmpl
  DEV["p-x-2.group-dev<br/>reopened to its assignee by the close hook"]:::poured
  SAN["p-x-2.group-sanity: gate<br/>left open, sanity_round + 1, re-blocked by dev"]:::poured
  MSG["atm send dev --template sanity-fail<br/>deliverable-2 NOT complete, deliverable-5 NOT complete"]:::script

  DEV ==>|parent-child| SPR
  SAN ==>|parent-child| SPR
  SAN ==>|blocks| DEV
  SAN -.->|hook sends| MSG
```

The sanity bead is a minimal gate with no `discovered-from` edge. On FAIL the
sanity close hook reopens the checked bead to its assignee, increments
`metadata.sanity_round` on the sanity bead, and sends the dev the
`sanity-fail` message naming each undone deliverable and its reason; no
finding children are created, the message and the sanity ledger are the
record. The dev bead holds the sprint open. The dev's ATM task was never
closed, so the dev fixes the undone deliverables on the same branch and
runs the dev close hook again; the hook re-assigns the same sanity task
(reopened in place). At `max_sanity_rounds` (repo config, default 5) the hook
reopens nothing and sends the lead `SANITY.ROUND_CAP`. The same applies to a
fix bead and its sanity.

dev-sanity is one teammate (`agents/dev-sanity.md`) that spawns
`sc-sanity-llm` and `sc-sanity-jev`, owns the verdict, reruns a failed
reviewer, and triages LLM/JEV disagreement.
