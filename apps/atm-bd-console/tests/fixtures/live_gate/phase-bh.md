# Phase BH live-gate transcript

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
