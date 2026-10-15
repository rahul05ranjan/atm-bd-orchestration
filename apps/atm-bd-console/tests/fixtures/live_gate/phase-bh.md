# Phase BH live-gate transcript

Installed from this branch with:

```text
UV_TOOL_DIR=/tmp/atm-bh10-r1-uv-tools UV_TOOL_BIN_DIR=/tmp/atm-bh10-r1-uv-bin \
  uv tool install --force /Users/randlee/github/atm-bd-orchestration-worktrees/fix/bh-10-sanity-r1/apps/atm-bd-console
```

Ran from `/Users/randlee/github/atm-core` with real `bd`, `atm`, `git`, and
GitHub GraphQL reads. Dispatches used `--dry-run`: ATM rejects a mutation when
`--as` does not match the session `ATM_IDENTITY` base agent.

```text
TERM=xterm-256color /tmp/atm-bh10-r1-uv-bin/atm-bd-console \
  --phase bh --as arch-ctm --once --interval 60 --dry-run
```

## Dispatch tab

```text
 Dispatch  Status  PRs
phase-bh · 05:45 · next 60s

· team-lead  0
● arch-ctm  1
 🌀 bh-10.group-dev.1       1m phase-bh: <atm-task id="atm-bh-10.group-dev.1" sprint="bh-10" mode="dev-fix">
◐ atm-sanity  4
 📥 bh-9.qa1-f1-r2-sanity   7s phase-bh: <atm-task id="atm-bh-9.qa1-f1-r2-sanity" sprint="bh-9" mode="dev-sanity">
· cipher  0
· cipher2  0
· dev-fast  0
· dev-opus  0
· fenix  0
· lumen  0
· publisher  0
· quality-mgr  0
· solar  0
· teammate  0
● trinity  2

== Errors (1)
 05:45 not_found: atm-bh-9.group-qa-f12-qa.metadata.sprint_bead; add the required ATM BD value

gh: graphql ✅ 05:45 | 9/12 [███████░░] 75% | active 0h00m | tick 05:45:16
```

## Status tab

```text
 Dispatch  Status  PRs
sprints 8/10 · ✅ 8 · 🌀 2 · ⏳ 0
▊ atm-bh-1   bh-1: contract: Result, source and tick types, JSON codec
▊ atm-bh-2   bh-2: bead-queues as a snapshot viewer, install check
▊ atm-bh-3   bh-3: ATM source: members, tasks, presence, send, assign
▊ atm-bh-4   bh-4: beads source and the atm-bd phase and formula mapping
▊ atm-bh-5   bh-5: git and GitHub sources: refs, merge check, worktrees, open PRs
▊ atm-bh-6   bh-6: tick rules: snapshot, holds, problems, agent status, metrics, stops
▊ atm-bh-7   bh-7: runtime files: JSON state, lock, config, logging
▊ atm-bh-8   bh-8: renderers: dashboard, status bar, Status DAG, PRs
▊ atm-bh-9   bh-9: runner.py, cli.py (headless, show), MVP gates
▊ atm-bh-10  bh-10: Textual app: Dispatch, Status DAG, PRs tabs
gh: graphql ✅ 05:45 | 9/12 [███████░░] 75% | active 0h00m | tick 05:45:16
```

## PRs tab after one refresh

```text
 Dispatch  Status  PRs
gh: graphql ✅ 05:45 | 9/12 [███████░░] 75% | active 0h00m | tick 05:45:38
```

## GitHub read count

```text
baseline=9 total=11 delta=2
2026-10-15T12:45:20.074787Z forced=false  initial live tick
2026-10-15T12:45:41.499184Z forced=true   one r on the PRs tab
```
