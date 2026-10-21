# Correlation: beads, ATM tasks and the current phase

```mermaid
flowchart LR
  classDef tmpl fill:#e8f0fe,stroke:#3367d6,color:#000
  classDef file fill:#f1f3f4,stroke:#5f6368,stroke-dasharray:4 3,color:#000
  classDef atm fill:#fef7e0,stroke:#f9ab00,color:#000

  subgraph ATMBD[".atm-bd/"]
    TOML[/"#lt;phase#gt;.toml (tracked)<br/>plan file, phase root, integration_branch"/]:::file
    FORM[/"formula/ (tracked): repo formula overrides"/]:::file
  end
  ROOT["p-phase-x root bead<br/>metadata.integration_branch"]:::tmpl
  SJ[/"#lt;plans_dir#gt;/#lt;phase#gt;.jsonl on the integration branch"/]:::file
  BEAD["bead under the root<br/>id = p-x-2.group-sanity"]:::tmpl
  TASK["ATM task<br/>task id = p-x-2.group-sanity"]:::atm

  TOML -. "equals (validate-plan)" .-> ROOT
  TOML -. "integration_branch" .-> SJ
  ROOT -->|"descendant (parent-child chain)"| BEAD
  BEAD <-. "bead id == ATM task id" .-> TASK
```

Tracked `.atm-bd/<phase>.toml` names the plan file, the phase root and the
integration branch; `validate-plan` checks that the integration branch equals
the root's `metadata.integration_branch`.
Work is assigned with `atm task assign <member> --task-id <bead id>`; the lead
picks the agent at dispatch.

| Bead | ATM task held by | Template |
| --- | --- | --- |
| dev | a dev, by `difficulty` | `dev-template.xml.j2` (a sanity FAIL reopens the same bead; `sanity-fail.xml.j2` goes to its assignee) |
| fix | a dev, by `difficulty` | `fix-assignment.xml.j2` |
| sanity | dev-sanity | `dev-sanity-template.xml.j2` |
| qa | quality-mgr | `qa-template.xml.j2` |
| important or minor finding | an idle dev, by priority | `fix-assignment.xml.j2` |
| sprint container | none; the team lead closes it | none |
