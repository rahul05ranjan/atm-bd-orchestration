# Lifecycle (sprint level)

```mermaid
sequenceDiagram
  autonumber
  participant L as team lead
  participant B as bd
  participant A as ATM
  participant D as dev
  participant S as dev-sanity
  participant Q as quality-mgr
  participant G as logs in .sc

  Note over L,B: planning: sprint containers by bd import
  L->>B: scripts/bead-groups: attach each sprint group with its relations
  Note over L,Q: plan review
  L->>B: bd ready
  B-->>L: dev (sanity and qa are blocked)
  L->>A: atm task assign D --task-id dev-bead-id
  D->>B: claim dev
  D->>A: atm task start dev-bead-id
  D->>B: bd close dev
  D->>A: atm task close dev-bead-id completed
  L->>B: bd ready
  B-->>L: sanity
  L->>A: atm task assign S --task-id sanity-bead-id
  S->>B: claim sanity
  Note over S: spawns sc-sanity-llm and sc-sanity-jev, owns the verdict
  S->>G: append two ledger rows (LLM, JEV) to .sc/sanity-log/phase-p.jsonl
  alt PASS
    S->>B: bd close sanity "PASS at sha"
  else FAIL
    S->>B: hook increments metadata.sanity_round
    alt round at most max_sanity_rounds (default 5)
      S->>B: hook reopens dev, resets sanity open and unassigned
      S->>D: hook sends sanity-fail: numbered undone deliverables and reasons
      Note over D: existing dev task stays open; fix, rebase, push, rerun hook
    else round exceeds max_sanity_rounds
      S->>L: hook sends SANITY.ROUND_CAP once
      Note over S,B: dev stays closed; sanity is not reset; no finding children
    end
  end
  S->>A: atm task close sanity-bead-id
  L->>B: bd ready
  B-->>L: qa
  L->>A: atm task assign Q --task-id qa-bead-id
  Q->>B: claim qa
  Q->>B: each blocking finding: bead-groups --findings pours a fix group onto the sprint
  Q->>B: important and minor findings: finding beads under the phase feature bead
  Q->>G: append .sc/qa-log/phase-p.jsonl and phase-p-stats.jsonl
  Q->>B: bd close qa
  Q->>A: atm task close qa-bead-id completed
  loop each open fix group, independently
    L->>A: atm task assign D --task-id fix-bead-id (fix-assignment.xml.j2)
    Note over L,Q: fix, sanity, qa run as dev, sanity, qa. The fix qa is the filing<br/>reviewer only. A fix qa FAIL pours round n+1, and nothing is reopened.
  end
  L->>B: close the sprint once every blocking fix group is closed
```

Each ATM task id equals its bead id (correlation.md).

| Fact | Where it is written |
| --- | --- |
| sanity result | sanity bead `close_reason` "PASS at sha"; FAIL increments `metadata.sanity_round` and sends numbered undone deliverables through `sanity-fail`, without finding children |
| QA verdict | qa bead `close_reason` |
| round | `metadata.round` on qa and fix beads |
| severity | `metadata.severity` on fix and finding beads |

dev-sanity appends the sanity ledger `.sc/sanity-log/phase-<p>.jsonl` with
`sanity-run-history`: two rows per run, one for LLM and one for JEV, sharing a
`run_id` and the final verdict. quality-mgr appends
`.sc/qa-log/phase-<p>.jsonl` and `.sc/qa-log/phase-<p>-stats.jsonl` when it
closes qa.
