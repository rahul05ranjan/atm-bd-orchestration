# Phase BH live-gate transcript

Commands in this transcript retain their historical syntax. For current
launches, configure `lead` and all seven template purposes in
`.atm-bd/phase-bh.toml`, then run
`atm-bd-console --phase bh --interval 600 --dry-run`; console `--as` was removed
by the phase-settings update.

This file has two parts. The first is the primary live transcript for
REFRESH-03 (`atm-phase-bh.refresh-f3`), taken at source SHA `6af2231085580d0bcba1c6234afcbc1840854062`. The second
retains the earlier R11 capture and the historical recovery evidence, verbatim.

## Current run (REFRESH-03): source, install and command

- Source SHA: `6af2231085580d0bcba1c6234afcbc1840854062`
- Installed version: `0.1.0` (`<scratch>/refresh-f3/bin/atm-bd-console --version`)
- Captured on 2026-10-15 (UTC times below) from one tmux session named `bhrf3`, 300x80.
- The console read the live `/Users/randlee/github/atm-core` checkout. GitHub reads
  went to `randlee/atm-bd-orchestration` through `GH_REPO`. `--dry-run` was set;
  every action logged in the window has `outcome: dry_run` (no dispatch mutations).
- Operator: cipher, the native operator authorized by the phase lead (solar) for this one capture.

Install (run once, from the worktree at the SHA above; the tool directories are scratch paths outside the repository):

```text
UV_TOOL_DIR=<scratch>/refresh-f3/tools UV_TOOL_BIN_DIR=<scratch>/refresh-f3/bin \
  uv tool install --force /Users/randlee/github/atm-bd-orchestration-worktrees/fix/bh-refresh-capture/apps/atm-bd-console
<scratch>/refresh-f3/bin/atm-bd-console --version
0.1.0
```

Session command, run in tmux pane `bhrf3`:

```text
cd /Users/randlee/github/atm-core && GH_REPO=randlee/atm-bd-orchestration <scratch>/refresh-f3/bin/atm-bd-console --phase bh --as solar --interval 600 --dry-run
```

`--interval 600` keeps an incidental interval tick out of the window (quoted actual
configured interval: 600s; the header shows `next 600s`).

## Key sequence (verbatim tmux commands, in the order sent)

Every input to the session was one of the `tmux send-keys -t bhrf3` commands
below, in this order. No other keys were sent. The launch line was sent
after the 18:51:45Z capture start; the keys after it were sent at the times shown.

```text
tmux send-keys -t bhrf3 "cd /Users/randlee/github/atm-core && GH_REPO=randlee/atm-bd-orchestration <scratch>/refresh-f3/bin/atm-bd-console --phase bh --as solar --interval 600 --dry-run" Enter
tmux send-keys -t bhrf3 Right     # sent just before 18:52:00Z (Dispatch -> Status; focus on the tab bar)
tmux send-keys -t bhrf3 Tab       # 18:52:07Z (focus moves to the sprint list)
tmux send-keys -t bhrf3 Down      # 18:52:08Z
tmux send-keys -t bhrf3 Down      # 18:52:09Z
tmux send-keys -t bhrf3 Down      # 18:52:09Z
tmux send-keys -t bhrf3 Down      # 18:52:10Z
tmux send-keys -t bhrf3 Down      # 18:52:11Z
tmux send-keys -t bhrf3 Down      # 18:52:11Z
tmux send-keys -t bhrf3 Down      # 18:52:12Z
tmux send-keys -t bhrf3 Down      # 18:52:13Z
tmux send-keys -t bhrf3 Down      # 18:52:13Z
tmux send-keys -t bhrf3 Enter     # 18:52:14Z (selected atm-bh-9: the list was one row above atm-bh-10)
tmux send-keys -t bhrf3 Down      # 18:52:22Z
tmux send-keys -t bhrf3 Enter     # 18:52:23Z (selected atm-bh-10; its steps are shown)
tmux send-keys -t bhrf3 BTab      # 18:52:33Z (focus back to the tab bar)
tmux send-keys -t bhrf3 Right     # 18:52:34Z (Status -> PRs; this key starts the forced read 1 tick)
tmux send-keys -t bhrf3 r         # 18:52:48Z (forced read 2 tick)
tmux send-keys -t bhrf3 q         # 18:53:00Z (quit)
```

Key times below are whole-second UTC. The session log recorded them with a
`date` format that macOS does not support, so fractional seconds were not
captured and none are claimed; the key order is exact.

Captures were taken with `tmux capture-pane -p -t bhrf3` (Dispatch, Status and
PRs), as described under Captures below.

Note: the first `Enter` selected `atm-bh-9`, not `atm-bh-10`, because the list
highlight was one row above `atm-bh-10` after nine Downs; the second `Down` and
`Enter` corrected it. Both selections are in the sequence and the Status
capture shows the final selection. Nothing was retyped or removed.

## Controlled reads and tick windows (verbatim console log lines)

Source: `/Users/randlee/github/atm-core/.atm-bd/atm-bd-console/logs/atm-bd-console.log.jsonl`
(the live checkout), filtered to `tick_start`, `tick_end` and `github_read`
events between 18:51:40Z and 18:53:10Z. Every `github_read` in that window is
listed; there are exactly three.

Read 1, unforced, from the initial tick (window 18:51:46.595124Z to 18:51:49.099755Z):

```json
{"version":"v1","timestamp":"2026-10-15T18:51:49.086053Z","level":"Info","service":"atm-bd-console","target":"atm_bd_console","action":"github_read","message":null,"identity":{"hostname":null,"pid":null},"trace":null,"request_id":null,"correlation_id":null,"outcome":null,"diagnostic":null,"state_transition":null,"fields":{"error":null,"forced":false,"outcome":"ok","sc_observability.binding.channel":"pyo3","sc_observability.binding.language":"python"}}
```

Read 2, forced, from the PRs activation (window 18:52:34.573912Z to 18:52:37.140667Z):

```json
{"version":"v1","timestamp":"2026-10-15T18:52:37.132141Z","level":"Info","service":"atm-bd-console","target":"atm_bd_console","action":"github_read","message":null,"identity":{"hostname":null,"pid":null},"trace":null,"request_id":null,"correlation_id":null,"outcome":null,"diagnostic":null,"state_transition":null,"fields":{"error":null,"forced":true,"outcome":"ok","sc_observability.binding.channel":"pyo3","sc_observability.binding.language":"python"}}
```

Read 3, forced, from the `r` press (window 18:52:48.324426Z to 18:52:50.740752Z):

```json
{"version":"v1","timestamp":"2026-10-15T18:52:50.737535Z","level":"Info","service":"atm-bd-console","target":"atm_bd_console","action":"github_read","message":null,"identity":{"hostname":null,"pid":null},"trace":null,"request_id":null,"correlation_id":null,"outcome":null,"diagnostic":null,"state_transition":null,"fields":{"error":null,"forced":true,"outcome":"ok","sc_observability.binding.channel":"pyo3","sc_observability.binding.language":"python"}}
```

All tick events in the window (the `tick_start` and `tick_end` bounds for the three windows):

```json
{"version":"v1","timestamp":"2026-10-15T18:51:46.595124Z","level":"Info","service":"atm-bd-console","target":"atm_bd_console","action":"tick_start","message":null,"identity":{"hostname":null,"pid":null},"trace":null,"request_id":null,"correlation_id":null,"outcome":null,"diagnostic":null,"state_transition":null,"fields":{"phase":"bh","sc_observability.binding.channel":"pyo3","sc_observability.binding.language":"python"}}
{"version":"v1","timestamp":"2026-10-15T18:51:49.099755Z","level":"Info","service":"atm-bd-console","target":"atm_bd_console","action":"tick_end","message":null,"identity":{"hostname":null,"pid":null},"trace":null,"request_id":null,"correlation_id":null,"outcome":null,"diagnostic":null,"state_transition":null,"fields":{"actions":8,"sc_observability.binding.channel":"pyo3","sc_observability.binding.language":"python"}}
{"version":"v1","timestamp":"2026-10-15T18:52:34.573912Z","level":"Info","service":"atm-bd-console","target":"atm_bd_console","action":"tick_start","message":null,"identity":{"hostname":null,"pid":null},"trace":null,"request_id":null,"correlation_id":null,"outcome":null,"diagnostic":null,"state_transition":null,"fields":{"phase":"bh","sc_observability.binding.channel":"pyo3","sc_observability.binding.language":"python"}}
{"version":"v1","timestamp":"2026-10-15T18:52:37.140667Z","level":"Info","service":"atm-bd-console","target":"atm_bd_console","action":"tick_end","message":null,"identity":{"hostname":null,"pid":null},"trace":null,"request_id":null,"correlation_id":null,"outcome":null,"diagnostic":null,"state_transition":null,"fields":{"actions":8,"sc_observability.binding.channel":"pyo3","sc_observability.binding.language":"python"}}
{"version":"v1","timestamp":"2026-10-15T18:52:48.324426Z","level":"Info","service":"atm-bd-console","target":"atm_bd_console","action":"tick_start","message":null,"identity":{"hostname":null,"pid":null},"trace":null,"request_id":null,"correlation_id":null,"outcome":null,"diagnostic":null,"state_transition":null,"fields":{"phase":"bh","sc_observability.binding.channel":"pyo3","sc_observability.binding.language":"python"}}
{"version":"v1","timestamp":"2026-10-15T18:52:50.740752Z","level":"Info","service":"atm-bd-console","target":"atm_bd_console","action":"tick_end","message":null,"identity":{"hostname":null,"pid":null},"trace":null,"request_id":null,"correlation_id":null,"outcome":null,"diagnostic":null,"state_transition":null,"fields":{"actions":8,"sc_observability.binding.channel":"pyo3","sc_observability.binding.language":"python"}}
```

Read 1 is the initial, unforced read that ran when the console started. Reads 2
and 3 are forced: read 2 ran when the PRs tab was activated, and read 3 ran on
the `r` press. Each completed (`tick_end`) before the next key was sent.

## Captures (verbatim pane payloads; full bytes and provenance headers in `raw/`)

Each block below is the exact payload of the matching raw file (the part after its
`# ---- payload follows` line). The raw files carry the source SHA, version,
command and capture time in their own headers.

### Dispatch: `raw/bh-refresh3-dispatch.txt`

Captured after the initial tick completed (tick_end 18:51:49.099755Z) and before the Right key. The pane shows `Held (12)` with exactly 12 rows; the count is the one shown in this viewport, not a stale fixed value. The Held list and Sent list are cropped to this viewport.

```text
 Dispatch  Status  PRs
╸━━━━━━━━╺━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
phase-bh · 11:51 · next 600s

· team-lead  0
· arch-ctm  0
· atm-sanity  0
● cipher  1
 🌀 phase-bh.refresh-f3    12s phase-bh: <atm-task id="atm-phase-bh.refresh-f3" sprint="bh-10" mode="fix"> <description><![CDATA[Fix `atm-phase-bh.refresh-f3` (found in sprint bh-10). `bd show atm-phase-bh.refresh-f3` is the assignment: a poured fix bead carries the finding in its description and `…
· cipher2  0
· dev-fast  0
· dev-opus  0
· fenix  0
· lumen  0
· publisher  0
· quality-mgr  0
■ solar  1
 🌀 phase-bh.refresh-f1     3m phase-bh: <atm-task id="atm-phase-bh.refresh-f1" sprint="phase-end" mode="fix"> <description><![CDATA[Fix `atm-phase-bh.refresh-f1` (found in sprint phase-end). `bd show atm-phase-bh.refresh-f1` is the assignment: a poured fix bead carries the finding in its descripti…
· teammate  0
· trinity  0

== Held (12)
  phase-bh.refresh-f3-qa   0s  not ready
  phase-bh.refresh-f3-qa   0s  branch not on origin: <missing>
  phase-bh.refresh-f3-qa   0s  no worktree for <missing>
  phase-bh.refresh-f3-sanity   0s  not ready
  phase-bh.refresh-f3-sanity   0s  branch not on origin: <missing>
  phase-bh.refresh-f3-sanity   0s  no worktree for <missing>
  phase-bh.refresh-f1-qa   0s  not ready
  phase-bh.refresh-f1-qa   0s  branch not on origin: <missing>
  phase-bh.refresh-f1-qa   0s  no worktree for <missing>
  phase-bh.refresh-f1-sanity   0s  not ready
  phase-bh.refresh-f1-sanity   0s  branch not on origin: <missing>
  phase-bh.refresh-f1-sanity   0s  no worktree for <missing>

== Sent
 11:51 phase-bh.refresh-f3-sanity → solar
 11:51 phase-bh.refresh-f1-qa → arch-ctm
 11:51 phase-bh.refresh-f1-qa → arch-ctm
 11:51 phase-bh.refresh-f1-sanity → solar
 11:51 phase-bh.refresh-f1-sanity → solar






































gh: graphql ✅ 11:51 | 10/10 [█████████] 100% | active 0h00m | tick 11:51:46
```

### Status: `raw/bh-refresh3-status.txt`

Captured at 18:52:29Z, after `atm-bh-10` was selected and before focus returned to the tab bar. The DAG is wider than the terminal and is cropped to this viewport, not a complete graph dump.

```text
 Dispatch  Status  PRs
━━━━━━━━━━╸━━━━━━╺━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
sprints 10/10 · ✅ 10 · 🌀 0 · ⏳ 0
▊▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▎
▊ atm-bh-1  bh-1: contract: Result, source and tick types, JSON codec                                                                                                                                                                                                                                      ▎
▊ atm-bh-2  bh-2: bead-queues as a snapshot viewer, install check                                                                                                                                                                                                                                          ▎
▊ atm-bh-3  bh-3: ATM source: members, tasks, presence, send, assign                                                                                                                                                                                                                                       ▎
▊ atm-bh-4  bh-4: beads source and the atm-bd phase and formula mapping                                                                                                                                                                                                                                    ▎
▊ atm-bh-5  bh-5: git and GitHub sources: refs, merge check, worktrees, open PRs                                                                                                                                                                                                                           ▎
▊ atm-bh-6  bh-6: tick rules: snapshot, holds, problems, agent status, metrics, stops                                                                                                                                                                                                                      ▎
▊ atm-bh-7  bh-7: runtime files: JSON state, lock, config, logging                                                                                                                                                                                                                                         ▎
▊ atm-bh-8  bh-8: renderers: dashboard, status bar, Status DAG, PRs                                                                                                                                                                                                                                        ▎
▊ atm-bh-9  bh-9: runner.py, cli.py (headless, show), MVP gates                                                                                                                                                                                                                                            ▎
▊ atm-bh-10  bh-10: Textual app: Dispatch, Status DAG, PRs tabs                                                                                                                                                                                                                                            ▎
▊▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▎
atm-bh-10  bh-10: Textual app: Dispatch, Status DAG, PRs tabs
  dev    atm-bh-10.group-dev  closed  arch-ctm
  qa     atm-bh-10.group-qa  closed  quality-mgr
  qa     atm-phase-bh.refresh-f3-qa  open  arch-ctm
  sanity atm-phase-bh.refresh-f3-sanity  open  unassigned
                                                                                                                                                                                                                ┌───────────────────────────────────────────────────────────────────────┐       ┌───────────
                                                                                                                                                                                                                │ ✅ atm-bh-1 bh-1: contract: Result, source and tick types, JSON codec │       │ ✅ atm-bh-
                                                                                                                                                                                                                └───────────────────────────────────────────────────────────────────────┘       └───────────
                                                                                                                                                                                                                 │             │             │             │             │             │
                                                                       ┌───────────────────────────────────────────────────────────────────────┬─────────────────────────────────────────────────────────────────┼─────────────┴─────────────┘             └─────────────┴─────────────┼────────────────────
                                                                       │                                                                       │                                                                 │                                                                     │
                                                                       ▼                                                                       ▼                                                                 ▼                                                                     ▼
┌──────────────────────────────────────────────────────────────────────┐       ┌─────────────────────────────────────────────────────────────────────────┐    ┌──────────────────────────────────────────────────────────────────────────────────┐    ┌─────────────────────────────────────────────────────
│ ✅ atm-bh-3 bh-3: ATM source: members, tasks, presence, send, assign │       │ ✅ atm-bh-4 bh-4: beads source and the atm-bd phase and formula mapping │    │ ✅ atm-bh-5 bh-5: git and GitHub sources: refs, merge check, worktrees, open PRs │    │ ✅ atm-bh-6 bh-6: tick rules: snapshot, holds, probl
└──────────────────────────────────────────────────────────────────────┘       └─────────────────────────────────────────────────────────────────────────┘    └──────────────────────────────────────────────────────────────────────────────────┘    └─────────────────────────────────────────────────────
                                                                       │                                                                  │                                                              │                                                                 │
                                                                       └──────────────────────────────────────────────────────────────────┴──────────────────┬──────────────────┐                  ┌─────┘            ┌──────────────────┬───────────────────┬─────────────┴────────────────────────────────
                                                                                                                                                             ▼                  ▼                  ▼                  ▼                  ▼                   ▼
                                                                                                                                                            ┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
                                                                                                                                                            │ ✅ atm-bh-9 bh-9: runner.py, cli.py (headless, show), MVP gates atm-phase-bh.end-f18 unassigned │
                                                                                                                                                            └─────────────────────────────────────────────────────────────────────────────────────────────────┘
                                                                                                                                                                                                             │
                                                                                                                                                                                                             │
                                                                                                                                                                                                             ▼
                                                                                                                                                            ┌────────────────────────────────────────────────────────────────────────────────────────────────┐
                                                                                                                                                            │ ✅ atm-bh-10 bh-10: Textual app: Dispatch, Status DAG, PRs tabs atm-phase-bh.refresh-f3 cipher │
                                                                                                                                                            └────────────────────────────────────────────────────────────────────────────────────────────────┘




































                                                                                                                                                                                         ▋
gh: graphql ✅ 11:51 | 10/10 [█████████] 100% | active 0h00m | tick 11:51:46
```

### PRs: `raw/bh-refresh3-prs.txt`

Captured after forced read 2 completed and before `q`. The PR pane is blank: it shows the header and footer only, with no rows. This is the honest result of the render for this run; the pane was not populated or edited.

```text
 Dispatch  Status  PRs
━━━━━━━━━━━━━━━━━━╸━━━╺━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━













































































gh: graphql ✅ 11:52 | 10/10 [█████████] 100% | active 0h00m | tick 11:52:48
```

## Historical evidence (retained, not the REFRESH-03 result)

- **48050ca (accepted transcript).** The earlier accepted transcript for the 08:09 recovery is in commit `48050ca`, at `apps/atm-bd-console/tests/fixtures/live_gate/phase-bh.md`. That run's build SHA is not recorded in this repository; it is not inferred here. It was captured from the installed bh-10 fix worktree (`fix/bh-10-qa-r4`).
- **bh10-0810 raw files.** `raw/bh10-0810-dispatch.txt`, `raw/bh10-0810-status.txt` and `raw/bh10-0810-prs.txt` are the approved 08:09 captures that the r3 `.1` notes and `48050ca` link to. They have no SHA header; their build SHA is not known and none is claimed. They are retained unchanged.
- **R11 (rdy-f11) capture.** The earlier 08:53 capture (`raw/bh-rdy-f11-*.txt`) is kept as historical evidence below, verbatim. It does not establish the Dispatch-first, completed-refresh-last order that r3 asked for: its PRs stamp 08:54:23 precedes the forced reads it is said to follow, and its transcript has no send-keys sequence. It is labelled historical; the REFRESH-03 transcript above is the current primary result.

## Previous revision (R11 capture, retained verbatim)


Source SHA: `d22382d7670a91255922e5f7c19ee06c02e73df8`
Installed version: `0.1.0`
Captured on: 2026-10-15 (PDT), from one real tmux session at 300×80.

The installed console ran against the live `/Users/randlee/github/atm-core`
checkout. GitHub reads were directed to `randlee/atm-bd-orchestration` with
`GH_REPO`; `--dry-run` prevented dispatch mutations. The package was installed
from this worktree at the source SHA above:

```text
UV_TOOL_DIR=/tmp/atm-phase-bh-rdy-f11-tools
UV_TOOL_BIN_DIR=/tmp/atm-phase-bh-rdy-f11-bin
uv tool install --force /Users/randlee/github/atm-bd-orchestration-worktrees/fix/bh-rdy-launch/apps/atm-bd-console
/tmp/atm-phase-bh-rdy-f11-bin/atm-bd-console --version
0.1.0
```

The installed command was:

```text
cd /Users/randlee/github/atm-core
GH_REPO=randlee/atm-bd-orchestration
/tmp/atm-phase-bh-rdy-f11-bin/atm-bd-console --phase bh --as fenix --interval 60 --dry-run
```

The actual pane captures are retained verbatim (with provenance headers) in
[`raw/bh-rdy-f11-dispatch.txt`](raw/bh-rdy-f11-dispatch.txt),
[`raw/bh-rdy-f11-status.txt`](raw/bh-rdy-f11-status.txt), and
[`raw/bh-rdy-f11-prs.txt`](raw/bh-rdy-f11-prs.txt). The Status pane shows
`atm-bh-10` selected. Its DAG exceeds the terminal width and is cropped; the
capture is a viewport, not a complete graph dump.

The live GitHub query returned three open PR rows in the cache, including the
BH stack PRs. The PR pane itself remained blank: the core phase plan has no
branch entries matching those PR heads, so the renderer had no rows to show.
The capture preserves this result rather than fabricating a populated pane.

## Controlled interactions and GitHub reads

The session began at 08:53 PDT. I captured Dispatch after its initial tick,
selected Status and `atm-bh-10`, opened PRs, pressed `r` once, and captured each
pane. These are every `github_read` log line during the controlled session
window (2026-10-15 15:53:23Z–15:54:31Z):

```text
{"version":"v1","timestamp":"2026-10-15T15:53:26.06801Z","level":"Info","service":"atm-bd-console","target":"atm_bd_console","action":"github_read","message":null,"identity":{"hostname":null,"pid":null},"trace":null,"request_id":null,"correlation_id":null,"outcome":null,"diagnostic":null,"state_transition":null,"fields":{"forced":false,"sc_observability.binding.channel":"pyo3","sc_observability.binding.language":"python"}}
{"version":"v1","timestamp":"2026-10-15T15:53:41.483683Z","level":"Info","service":"atm-bd-console","target":"atm_bd_console","action":"github_read","message":null,"identity":{"hostname":null,"pid":null},"trace":null,"request_id":null,"correlation_id":null,"outcome":null,"diagnostic":null,"state_transition":null,"fields":{"forced":true,"sc_observability.binding.channel":"pyo3","sc_observability.binding.language":"python"}}
{"version":"v1","timestamp":"2026-10-15T15:54:25.627597Z","level":"Info","service":"atm-bd-console","target":"atm_bd_console","action":"github_read","message":null,"identity":{"hostname":null,"pid":null},"trace":null,"request_id":null,"correlation_id":null,"outcome":null,"diagnostic":null,"state_transition":null,"fields":{"forced":false,"sc_observability.binding.channel":"pyo3","sc_observability.binding.language":"python"}}
{"version":"v1","timestamp":"2026-10-15T15:54:31.454158Z","level":"Info","service":"atm-bd-console","target":"atm_bd_console","action":"github_read","message":null,"identity":{"hostname":null,"pid":null},"trace":null,"request_id":null,"correlation_id":null,"outcome":null,"diagnostic":null,"state_transition":null,"fields":{"forced":true,"sc_observability.binding.channel":"pyo3","sc_observability.binding.language":"python"}}
```
