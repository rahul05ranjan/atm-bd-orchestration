# Correlation: beads, ATM tasks and the current phase

```mermaid
flowchart LR
  classDef tmpl fill:#e8f0fe,stroke:#3367d6,color:#000
  classDef file fill:#f1f3f4,stroke:#5f6368,stroke-dasharray:4 3,color:#000
  classDef atm fill:#fef7e0,stroke:#f9ab00,color:#000

  subgraph ATMBD[".atm-bd/"]
    TOML[/"#lt;phase#gt;.toml (tracked)<br/>plan, root, integration_branch, lead, templates"/]:::file
    FORM[/"formula/ (tracked): repo formula overrides"/]:::file
    CACHE[/"#lt;dolt_database#gt;.bd-cache.sqlite3 (ignored)<br/>cached rows and the bead write journal"/]:::file
    SCJ[/"sanity-close/ (ignored)<br/>sanity hook close plans and escalations"/]:::file
  end
  ROOT["p-phase-x root bead<br/>metadata.integration_branch"]:::tmpl
  SJ[/"#lt;plans_dir#gt;/#lt;phase#gt;.jsonl on the integration branch"/]:::file
  BEAD["bead under the root<br/>id = p-x-2.group-sanity"]:::tmpl
  TASK["ATM task<br/>task id = p-x-2.group-sanity"]:::atm
  ESC["ATM escalation task<br/>p-x-2.group-sanity.close-error-id, no bead"]:::atm

  TOML -. "equals (validate-plan)" .-> ROOT
  TOML -. "integration_branch" .-> SJ
  TOML -. "lead: receives escalations" .-> ESC
  ROOT -->|"descendant (parent-child chain)"| BEAD
  BEAD <-. "bead id == ATM task id" .-> TASK
  CACHE -. "row and pending writes of" .-> BEAD
  TASK -. "a failed sanity close" .-> ESC
```

Tracked `.atm-bd/<phase>.toml` names the plan file, the phase root, the
integration branch, the phase lead and the assignment template for each
purpose (`[templates]`: `dev`, `fix`, `sanity`, `qa`, `plan_review`,
`review`); `validate-plan` checks that the integration branch equals the
root's `metadata.integration_branch`. The close hooks and the bd-cache cron
send their escalations to that `lead`.
Work is assigned with `atm task assign <member> --task-id <bead id>`; the lead
picks the agent at dispatch. The sanity task is the exception: the dev's
close script assigns it, as the lead, to the dev-sanity member.

| Bead | ATM task held by | Assigned by, template |
| --- | --- | --- |
| dev | a dev, by `difficulty` | the lead, `templates.dev`; a sanity FAIL reopens the same bead and the lead re-assigns the same task, with no new template. The sanity close hook (`sanity_task_close_hook.py`) journals the reopen through the bd-cache and sends the dev `sanity-failed-template.md.j2`; a legacy-mode close (`atm-task-close.py --role sanity`) sends `sanity-fail.xml.j2` |
| fix | a dev, by `difficulty` | the lead, `templates.fix` |
| sanity | dev-sanity | `atm-task-close.py` at dev or fix close, `dev-sanity-template.xml.j2` |
| qa | quality-mgr | the lead, `templates.qa`, vars from `close-vars.py --qa-assignment` |
| important or minor finding | an idle dev, by priority | the lead, `templates.fix` |
| sprint container | none; the team lead closes it | none |
| none: `<sanity task>.close-error-<id>` | the phase lead | the sanity close hook, `hook-close-escalation.xml.j2`, one per cause |
