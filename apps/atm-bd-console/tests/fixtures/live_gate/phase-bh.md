# Phase BH live-gate transcript

Captured on 2026-10-15 from an installed copy of this branch in a real tmux
session. The app reads the live `/Users/randlee/github/atm-core` checkout;
`--dry-run` prevents the console from dispatching any ATM mutations.

Installed with:

```text
UV_TOOL_DIR=/tmp/atm-bh10-f1-r4-tools UV_TOOL_BIN_DIR=/tmp/atm-bh10-f1-r4-bin \
  uv tool install --force /Users/randlee/github/atm-bd-orchestration-worktrees/fix/bh-10-qa-r4/apps/atm-bd-console
```

Started in tmux with:

```text
cd /Users/randlee/github/atm-core && TERM=xterm-256color \
  /tmp/atm-bh10-f1-r4-bin/atm-bd-console --phase bh --as arch-ctm --interval 60 --dry-run
```

Exact controlled tmux interaction after the initial tick completed:

```text
tmux capture-pane -pt bh10r3clean -S -200                             # Dispatch first
tmux send-keys -t bh10r3clean -l $'\033[<0;17;1M\033[<0;17;1m'  # Status
tmux send-keys -t bh10r3clean -l $'\033[<0;20;14M\033[<0;20;14m'  # select atm-bh-10 (no forced read)
tmux capture-pane -pt bh10r3clean -S -200                             # Status and selected steps
tmux send-keys -t bh10r3clean -l $'\033[<0;23;1M\033[<0;23;1m'  # PRs, once
tmux send-keys -t bh10r3clean r                                      # refresh, once
tmux capture-pane -pt bh10r3clean -S -200                             # PRs pane
tmux send-keys -t bh10r3clean q                                      # quit
```

No tab switch occurred after PRs activation.

The verbatim pane captures from this one `08:09` session are retained in
`raw/bh10-0810-dispatch.txt`, `raw/bh10-0810-status.txt`, and
`raw/bh10-0810-prs.txt` for direct comparison.

## Dispatch tab

```text
 Dispatch  Status  PRs
╸━━━━━━━━╺━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
phase-bh · 08:09 · next 60s

· team-lead  0
● arch-ctm  1
 🌀 bh-10.qa1-f1-r3-fix.1  23s phase-bh: <atm-task id="atm-bh-10.qa1-f1-r3-fix.1" sprint="bh-10" mode="dev-fix">
· atm-sanity  0
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
· trinity  0

== Held (6)
  bh-10.qa1-f1-r3-qa       0s  not ready
  bh-10.qa1-f1-r3-qa       0s  branch not on origin: <missing>
  bh-10.qa1-f1-r3-qa       0s  no worktree for None
  bh-10.qa1-f1-r3-sanity   0s  not ready
  bh-10.qa1-f1-r3-sanity   0s  branch not on origin: <missing>
  bh-10.qa1-f1-r3-sanity   0s  no worktree for None

gh: graphql ✅ 08:09 | 10/11 [████████░] 91% | active 0h00m | tick 08:09:36
```

## Status tab and selected sprint

The tmux mouse interaction selected the Status tab and then selected
`atm-bh-10` in the live sprint list:

This is a terminal viewport capture, not a claim of a complete graph dump:
the right side of the rendered DAG is cropped at the tmux width. Likewise,
the live Dispatch pane reports `Held (6)`, and its complete six held rows are
shown in the captured viewport.

```text
 Dispatch  Status  PRs
━━━━━━━━━━╸━━━━━━╺━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
sprints 9/10 · ✅ 9 · 🌀 1 · ⏳ 0
▊ atm-bh-1  bh-1: contract: Result, source and tick types, JSON codec
▊ atm-bh-2  bh-2: bead-queues as a snapshot viewer, install check
▊ atm-bh-3  bh-3: ATM source: members, tasks, presence, send, assign
▊ atm-bh-4  bh-4: beads source and the atm-bd phase and formula mapping
▊ atm-bh-5  bh-5: git and GitHub sources: refs, merge check, worktrees, open PRs
▊ atm-bh-6  bh-6: tick rules: snapshot, holds, problems, agent status, metrics, stops
▊ atm-bh-7  bh-7: runtime files: JSON state, lock, config, logging
▊ atm-bh-8  bh-8: renderers: dashboard, status bar, Status DAG, PRs
▊ atm-bh-9  bh-9: runner.py, cli.py (headless, show), MVP gates
▊ atm-bh-10  bh-10: Textual app: Dispatch, Status DAG, PRs tabs
atm-bh-10  bh-10: Textual app: Dispatch, Status DAG, PRs tabs
  dev    atm-bh-10.group-dev  closed  arch-ctm
  qa     atm-bh-10.group-qa  closed  quality-mgr
  fix    atm-bh-10.qa1-f1-r3-fix  open  arch-ctm
  qa     atm-bh-10.qa1-f1-r3-qa  open  unassigned
gh: graphql ✅ 08:09 | 10/11 [████████░] 91% | active 0h00m | tick 08:09:36
```

## PRs tab after refresh

The PRs tab was opened by tmux mouse click, then `r` was sent once. The live
repository returned an empty PR pane (there is no rendered PR row or
empty-state text between the tab divider and the status bar):

```text
 Dispatch  Status  PRs
━━━━━━━━━━━━━━━━━━╸━━━╺━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━



gh: graphql ✅ 08:09 | 10/11 [████████░] 91% | active 0h00m | tick 08:09:51
```

## Raw GitHub reads and interaction sequence

These are every verbatim `github_read` log lines from the controlled capture
window in
`/Users/randlee/github/atm-core/.atm-bd/atm-bd-console/logs/atm-bd-console.log.jsonl`.
The initial tick is the sole non-forced read. The following two reads are,
in order, the one PRs activation and the one `r`; the session then quit.

```text
{"version":"v1","timestamp":"2026-10-15T15:09:39.755027Z","level":"Info","service":"atm-bd-console","target":"atm_bd_console","action":"github_read","message":null,"identity":{"hostname":null,"pid":null},"trace":null,"request_id":null,"correlation_id":null,"outcome":null,"diagnostic":null,"state_transition":null,"fields":{"forced":false,"sc_observability.binding.channel":"pyo3","sc_observability.binding.language":"python"}}
{"version":"v1","timestamp":"2026-10-15T15:09:50.537686Z","level":"Info","service":"atm-bd-console","target":"atm_bd_console","action":"github_read","message":null,"identity":{"hostname":null,"pid":null},"trace":null,"request_id":null,"correlation_id":null,"outcome":null,"diagnostic":null,"state_transition":null,"fields":{"forced":true,"sc_observability.binding.channel":"pyo3","sc_observability.binding.language":"python"}}
{"version":"v1","timestamp":"2026-10-15T15:09:54.715871Z","level":"Info","service":"atm-bd-console","target":"atm_bd_console","action":"github_read","message":null,"identity":{"hostname":null,"pid":null},"trace":null,"request_id":null,"correlation_id":null,"outcome":null,"diagnostic":null,"state_transition":null,"fields":{"forced":true,"sc_observability.binding.channel":"pyo3","sc_observability.binding.language":"python"}}
```
