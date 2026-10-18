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
Work is assigned with `atm task assign <member> --task-id <bead id>`. The
lead picks the dev for a dev or fix bead; sanity and qa beads are assigned as
the lead by the close hook (`atm-task-close.py`, [requirements/task-close-hook.md](../requirements/task-close-hook.md))
the moment the bead below them closes, so the ATM task id is always the bead
id and a re-assignment after a sanity FAIL reopens the same task in place.

| Bead | ATM task held by | Template |
| --- | --- | --- |
| dev | a dev, by `difficulty`; the task stays open until sanity PASS | `dev-template.xml.j2` (`sanity-fail.xml.j2` after a sanity FAIL, `rebase-required.xml.j2` after a lost race for the top, same task) |
| fix | a dev, by `difficulty` | `fix-assignment.xml.j2` |
| sanity | dev-sanity, assigned by the dev's close hook as the lead | `dev-sanity-template.xml.j2` |
| qa | quality-mgr, assigned by the sanity close hook as the lead | `qa-template.xml.j2` |
| important or minor finding | an idle dev, by priority | `fix-assignment.xml.j2` |
| sprint container | none; the team lead closes it | none |
| stack bead (`<prefix>-phase-<x>.stack`) | none; its claim is the stack lock, held by sanity-stack or the lead for one stack write; closed when the stack lands | none |
