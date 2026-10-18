# Lifecycle (sprint level)

Dev work is complete when its sanity check passes, not when the dev pushes.
The dev's ATM task stays open while sanity runs (about two minutes; a
reminder during the wait is expected and answered by waiting). Sanity
assignment, QA assignment and the close of the dev's task are mechanical: the
close hook `atm-task-close.py` ([requirements/task-close-hook.md](../requirements/task-close-hook.md))
runs them as the phase lead the moment the verdict is known. Dev and fix
assignment stay the lead's judgment.

```mermaid
sequenceDiagram
  autonumber
  participant L as team lead
  participant B as bd
  participant A as ATM
  participant D as dev
  participant H as close hook (atm-task-close.py)
  participant S as dev-sanity
  participant Q as quality-mgr
  participant G as logs in .sc

  Note over L,B: planning: sprint containers by bd import; bead-groups pours dev, sanity and qa with branch, worktree, pr_target and difficulty on all three
  Note over L,Q: plan review
  L->>B: bd ready
  B-->>L: dev (sanity and qa are blocked)
  L->>A: atm task assign D --task-id dev-bead-id (dev-template)
  D->>B: claim dev
  D->>A: atm task start dev-bead-id
  Note over D: implement, test, push, open the PR on pr_target
  D->>H: --role dev --vars shared-vars.json
  Note over H: vars pass the header-only template (strict); PR open on pr_target, pushed head is the PR head, origin/pr_target is an ancestor; GitHub unreachable means local checks only
  H->>B: bd update dev --set-metadata commit, pr_number, pr_url; bd close dev
  H->>A: atm send L dev-complete report (a plain send, not a close)
  H->>A: atm task assign S --task-id sanity-bead-id (ATM_IDENTITY = lead)
  Note over D: dev task stays open; the dev waits for the verdict
  S->>B: claim sanity
  S->>A: atm task start sanity-bead-id
  Note over S: spawns sc-sanity-llm and sc-sanity-jev, owns the verdict
  S->>G: append two ledger rows (LLM, JEV) to .sc/sanity-log/phase-p.jsonl
  S->>H: --role sanity --vars shared-vars.json (verdict, undone deliverables)
  alt PASS
    H->>B: bd close sanity "PASS at sha"
    H->>A: atm task close dev-bead-id completed (ATM_IDENTITY = lead)
    H->>A: atm task close sanity-bead-id completed
    H->>A: atm task assign Q --task-id qa-bead-id (ATM_IDENTITY = lead)
    Note over D: next queued task starts
    L->>L: sanity PASS report: link the layer PR on the phase stack
  else FAIL below max_sanity_rounds
    H->>B: sanity_round + 1 on the sanity bead; reopen dev to its assignee
    H->>A: atm send D --template sanity-rejected (undone deliverables)
    H->>A: atm task close sanity-bead-id completed (FAIL)
    Note over D: fix on the same branch and task, run --role dev again
  else FAIL at max_sanity_rounds (default 5)
    H->>A: atm send L SANITY.ROUND_CAP; nothing reopened; the dev task stays open until the lead rules
  end
  Note over L,A: fallback: a sanity or qa bead that bd ready lists with no ATM task is assigned by the console or the lead as the lead
  Q->>B: claim qa
  Q->>A: atm task start qa-bead-id
  Q->>B: each blocking finding: bead-groups --findings pours a fix group onto the sprint
  Q->>B: important and minor findings: finding beads under the phase feature bead
  Q->>G: append .sc/qa-log/phase-p.jsonl and phase-p-stats.jsonl
  Q->>H: --role qa --vars shared-vars.json
  H->>B: bd close qa
  H->>A: atm task close qa-bead-id completed
  loop each open fix group, independently
    L->>A: atm task assign D --task-id fix-bead-id (fix-assignment.xml.j2)
    Note over L,Q: fix, sanity, qa run as dev, sanity, qa through the same hook roles. The fix qa is the filing<br/>reviewer only. A fix qa FAIL pours round n+1, and nothing is reopened.
  end
  L->>B: close the sprint once every blocking fix group is closed
```

Each ATM task id equals its bead id (correlation.md).

| Fact | Where it is written |
| --- | --- |
| dev or fix done | `commit`, `pr_number`, `pr_url` in the bead's metadata, written by the close hook before `bd close` |
| sanity result | sanity bead `close_reason` "PASS at sha"; FAIL: `metadata.sanity_round` on the sanity bead and the sanity-rejected message to the dev |
| dev task close | by the close hook, as the lead, on sanity PASS |
| QA verdict | qa bead `close_reason` |
| round | `metadata.round` on qa and fix beads; `metadata.sanity_round` on a sanity bead |
| severity | `metadata.severity` on fix and finding beads |

dev-sanity appends the sanity ledger `.sc/sanity-log/phase-<p>.jsonl` with
`sanity-run-history`: two rows per run, one for LLM and one for JEV, sharing a
`run_id` and the final verdict. quality-mgr appends
`.sc/qa-log/phase-<p>.jsonl` and `.sc/qa-log/phase-<p>-stats.jsonl` when it
closes qa.
