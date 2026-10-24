# Lifecycle (sprint level)

```mermaid
sequenceDiagram
  autonumber
  participant L as team lead
  participant B as bd
  participant A as ATM
  participant D as dev
  participant C as atm-task-close.py
  participant S as dev-sanity
  participant H as sanity close hook
  participant Q as quality-mgr
  participant G as logs in .sc

  Note over L,B: planning: sprint containers by bd import
  L->>B: scripts/bead-groups: attach each sprint group with its relations and close fields
  Note over L,Q: plan review
  L->>B: bd ready
  B-->>L: dev (sanity and qa are blocked)
  L->>A: atm task assign D --task-id dev-bead-id (templates.dev, close vars file)
  D->>B: claim dev
  D->>A: atm task start dev-bead-id
  D->>C: --role dev --vars (lead's close vars) --result (dev's own fields)
  C->>B: journal: record commit, branch and PR on dev, close dev (drained at once)
  C->>L: dev-complete report
  C->>A: assign sanity to dev-sanity (as the lead), close the dev task
  S->>B: claim sanity
  S->>A: atm task close sanity-bead-id completed --template sanity-task-close.md.j2
  A->>H: task.close
  alt PASS
    H->>B: journal: close sanity "PASS at sha"
    H->>A: close the dev task if still open, then the sanity task
    Note over S: fixed PASS: stack input to sanity-stack, which links the layer
  else FAIL
    H->>B: journal: reopen dev, set sanity open and unassigned (still blocked by dev)
    H->>D: sanity-failed: numbered missing items
    H->>A: close the sanity task
    Note over L,D: lead re-assigns the dev task: fix, rebase, push, rerun atm-task-close.py
  else invalid result or hook error
    H->>L: escalation task sanity-bead-id.close-error-id (no bead write, the task stays open)
  end
  L->>B: bd ready (once the drain has applied the close)
  B-->>L: qa
  L->>A: close-vars.py qa-bead-id --qa-assignment, then atm task assign Q
  Q->>B: claim qa
  Q->>B: each blocking finding: bead-groups --findings pours a fix group onto the sprint
  Q->>B: important and minor findings: finding beads under the phase feature bead
  Q->>G: append .sc/qa-log/phase-p.jsonl and phase-p-stats.jsonl
  Q->>C: --role qa --vars (close vars)
  C->>B: journal: close qa "verdict at sha"
  C->>L: QA verdict message, close the qa task
  loop each open fix group, independently
    L->>A: atm task assign D --task-id fix-bead-id (templates.fix)
    Note over L,Q: fix, sanity, qa run as dev, sanity, qa. The fix qa is the filing<br/>reviewer only. A fix qa FAIL pours round n+1, and nothing is reopened.
  end
  L->>B: close the sprint once every blocking fix group is closed
```

Each ATM task id equals its bead id (correlation.md). The close script and
the hook write beads only through the bd-cache journal (bd-cache.md); `bd
ready` sees a write once the drain applies it, normally under a second. The
close paths in detail, and where the close vars come from, are in
close-hooks.md.

This is the daemon close mode: the phase registers the sanity `task.close`
hook and the sanity assignment's `sanity_close_mode` is `daemon`. In the
legacy mode (no registration) dev-sanity closes with `sanity-close-vars` and
`atm-task-close.py --role sanity`, which on PASS also assigns QA and on FAIL
counts rounds (close-hooks.md, "Legacy sanity close").

| Fact | Where it is written |
| --- | --- |
| close fields | every open dev, fix, sanity and qa bead's metadata, from the pour (`CloseFields`) |
| recorded close | the checked bead's `metadata.commit`, `branch`, `pr_number`, `pr_url`, by `atm-task-close.py`; QA vars are built from them |
| sanity result | sanity bead `close_reason` "PASS at sha"; FAIL reopens the checked bead and sends the numbered missing items through `sanity-failed`, without finding children |
| QA verdict | qa bead `close_reason` "verdict at sha" |
| round | `metadata.round` on qa and fix beads |
| severity | `metadata.severity` on fix and finding beads |
| bead writes from a close | bd-cache journal `.atm-bd/<dolt_database>.bd-cache.sqlite3` |
| sanity hook close plans and escalations | `.atm-bd/sanity-close/journal.sqlite3` |

quality-mgr appends `.sc/qa-log/phase-<p>.jsonl` and
`.sc/qa-log/phase-<p>-stats.jsonl` when it closes qa. In the legacy close
mode dev-sanity also appends the sanity ledger `.sc/sanity-log/phase-<p>.jsonl`
with `sanity-run-history`: two rows per run, one for LLM and one for JEV,
sharing a `run_id` and the final verdict.
