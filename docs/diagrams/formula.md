# Formulas and pouring

Formulas live in the package at
`plugins/atm-bd-orchestration/skills/atm-bd-orchestration/formulas/`, two files
each: `<name>.formula.toml.j2` (the sc-compose source) and
`<name>.relations.json` (attach ref and attach relations). A file of the same
name in the repository's `.atm-bd/formula/` (tracked) overrides the package
one.

Pouring has three stages:

1. `sc-compose bead attach` pours the formula under its parent, schema
   `sc-compose/beads/v1`.
2. The same attach adds the request's `relations`, the edges a formula
   cannot express: `validates`, `discovered-from` and cross-sprint `blocks`.
   A relation's bead must already exist, so `bead-groups` attaches sprints
   in dependency order, then findings.
3. Everything else is created from templates or by package scripts.

`scripts/bead-groups` runs stages 1 and 2. Targets: one sprint (`--sprint`), a
list (`--sprint a,b`), a whole phase (`--phase`), or one QA round's blocking
findings (`--findings FILE`). `--validate` (`sc-compose bead preview-attach`) changes no bead and reports what is
missing or wrong. It creates only what is missing, so it is safe to re-run
after sprints are added. Requests and receipts go to `.atm-bd/pour/`
(ignored).

## Stages and inputs

```mermaid
flowchart TB
  classDef file fill:#f1f3f4,stroke:#5f6368,stroke-dasharray:4 3,color:#000
  classDef poured fill:#f3e8fd,stroke:#8430ce,stroke-width:3px,stroke-dasharray:6 3,color:#000
  classDef post fill:#fff8e1,stroke:#b06000,stroke-dasharray:1 3,color:#000
  classDef tmpl fill:#e8f0fe,stroke:#3367d6,color:#000

  PKG[/"package formulas/<br/>sprint-group, finding-group"/]:::file
  OVR[/".atm-bd/formula/ (tracked)<br/>repo override"/]:::file
  PLAN["planning: sprint containers by bd import,<br/>plan file #lt;plans_dir#gt;/#lt;phase#gt;.jsonl committed"]:::tmpl
  QM["quality-mgr at QA FAIL, before closing qa:<br/>bead-groups --findings"]:::tmpl
  BG["scripts/bead-groups<br/>one sprint, a list, a phase, or findings<br/>--validate, idempotent"]:::post
  REQ[/"request: sc-compose/beads/v1<br/>compose_variables, parent, ref, relations"/]:::file
  POUR["stage 1: sc-compose bead attach<br/>beads, parent-child to the parent,<br/>blocks between its own steps"]:::poured
  PP["stage 2: attach relations: validates, discovered-from,<br/>cross-sprint blocks (from the plan file)"]:::post
  REVIEW["plan review"]:::tmpl

  PLAN --> BG
  QM --> BG
  BG --> REQ
  PKG --> REQ
  OVR -. "used instead when present" .-> REQ
  REQ --> POUR
  POUR --> PP
  PP --> REVIEW
```

A formula step can set `title`, `type`, `labels`, `metadata`, `priority` and
`needs` (sibling steps only, poured as `blocks`). So `validates`,
`discovered-from` and edges to beads outside the formula are stage 2. Poured
ids are `<parent>.<ref>-<step>`.

## Sprint formula

```mermaid
flowchart TB
  classDef tmpl fill:#e8f0fe,stroke:#3367d6,color:#000
  classDef poured fill:#f3e8fd,stroke:#8430ce,stroke-width:3px,stroke-dasharray:6 3,color:#000

  SPR["p-x-2 sprint container<br/>parent = p-x-2, ref = group"]:::tmpl
  subgraph POUR["poured: sprint-group"]
    DEV["dev: p-x-2.group-dev<br/>metadata: phase, sprint, stack, layer, difficulty, sprint_bead"]:::poured
    SAN["sanity: p-x-2.group-sanity, needs dev<br/>metadata.dev_bead"]:::poured
    QA["qa: p-x-2.group-qa, needs sanity<br/>metadata: checked_bead, round = 1"]:::poured
  end
  PRED["predecessor's initial sanity bead"]:::tmpl

  DEV ==>|parent-child| SPR
  SAN ==>|parent-child| SPR
  QA ==>|parent-child| SPR
  SAN ==>|blocks| DEV
  QA ==>|blocks| SAN
  QA -.->|validates| DEV
  DEV -.->|blocks| PRED
```

## Finding formula: one blocking finding

```mermaid
flowchart TB
  classDef tmpl fill:#e8f0fe,stroke:#3367d6,color:#000
  classDef poured fill:#f3e8fd,stroke:#8430ce,stroke-width:3px,stroke-dasharray:6 3,color:#000
  classDef closed fill:#e0e0e0,stroke:#757575,color:#000

  SPR["p-x-2 sprint container<br/>parent = p-x-2, ref = qa1-f3-r1"]:::tmpl
  QA1["p-x-2.group-qa (FAIL)<br/>filed_by; closed after this pour"]:::closed
  subgraph POUR["poured by quality-mgr: finding-group"]
    FX["fix: p-x-2.qa1-f3-r1-fix<br/>finding_ref, severity, reviewer, remedy,<br/>filed_by, round, difficulty"]:::poured
    FS["sanity: p-x-2.qa1-f3-r1-sanity, needs fix<br/>metadata.dev_bead"]:::poured
    FQ["qa: p-x-2.qa1-f3-r1-qa, needs sanity<br/>checked_bead, reviewer, round"]:::poured
  end

  FX ==>|parent-child| SPR
  FS ==>|parent-child| SPR
  FQ ==>|parent-child| SPR
  FS ==>|blocks| FX
  FQ ==>|blocks| FS
  FQ -.->|validates| FX
  FX -.->|discovered-from| QA1
```

Only `severity = blocking` is poured; `bead-groups` refuses others. It also
refuses to pour once the `filed_by` qa bead is closed, so quality-mgr pours
first and closes qa after. A second round is the same formula with
`round = n+1`.

## Idempotent attach

```mermaid
flowchart TB
  classDef ok fill:#e6f4ea,stroke:#188038,color:#000
  classDef bad fill:#fce8e6,stroke:#c5221f,color:#000

  REQ["request: parent, formula, vars"]
  PREV["preview: beads and edges, no write"]
  AUTH{"pour authorized?<br/>(not --validate)"}
  R0["report only"]:::ok
  EACH["each expected bead and edge"]
  EXISTS{"exists?"}
  SAME{"same formula revision<br/>and type?"}
  NOOP["no-op: never reset status,<br/>notes or claims"]:::ok
  CONFLICT["refuse the target"]:::bad
  CREATE["create only what is missing"]:::ok
  RCPT["receipt: poured ids"]:::ok

  REQ --> PREV --> AUTH
  AUTH -- no --> R0
  AUTH -- yes --> EACH --> EXISTS
  EXISTS -- yes --> SAME
  SAME -- yes --> NOOP
  SAME -- no --> CONFLICT
  EXISTS -- no --> CREATE
  NOOP --> RCPT
  CREATE --> RCPT
```

A run that stopped partway resumes by creating only what is missing. An edge
that exists with another type (bd keeps one type per pair) or a bead at a
poured id from another formula revision refuses that target before its edges
are written.
