# Bead relationship diagrams

The sprint bead is a container with three sibling children, `dev ← sanity ←
qa`. Each blocking finding pours a flat `fix ← sanity ← qa` group as siblings
under the same sprint (one finding, one fix); the fix bead is the dev's task
and carries the finding's metadata. bd refuses to close a parent with open
children, and the team lead closes the sprint when every blocking fix group is
closed. Important and minor findings are plain finding beads against the phase
or feature bead, picked up by idle devs by priority; they never block a
sprint. Dependencies are only hard (the dependent sprint needs the
predecessor's API): the dependent dev bead blocks on the predecessor's initial
sanity bead; an edge to the predecessor's sprint bead is added only on the
user's request. Every open dev, fix, sanity and qa bead carries the close
fields its close needs. Closes run through `atm-task-close.py` (dev, fix, qa)
and the sanity `task.close` hook, and every bead write they make goes through
the bd-cache journal.

| File | Level | Shows |
| --- | --- | --- |
| [phase.md](phase.md) | phase | root, sprint containers, poured groups, cross-sprint edges, phase-wide beads, non-blocking findings |
| [sprint.md](sprint.md) | sprint | the sprint group, fix groups per blocking finding, a second fix round, sanity FAIL, closers |
| [lifecycle.md](lifecycle.md) | sprint | ready, claim and close order; where results and logs are written |
| [close-hooks.md](close-hooks.md) | detail | dev or fix close script, the sanity `task.close` hook (PASS, FAIL, escalation), the legacy sanity close, close vars and the manual close |
| [bd-cache.md](bd-cache.md) | detail | cached reads, journaled writes, the drain, journal entry states, the cron tick, sync and outage |
| [correlation.md](correlation.md) | phase | bead id equals ATM task id, `.atm-bd/` files, who assigns and holds each task, escalation tasks |
| [formula.md](formula.md) | detail | pour stages, formula location and override, close fields, the beads and edges each formula pours, idempotent attach |

## Conventions

- `p` is a neutral bead prefix; `p-phase-x` is a phase root and `p-x-2` a
  sprint container.
- An arrow goes from the bead that holds the dependency to the bead it depends
  on, labeled with the bd dependency type. `A -->|blocks| B` means A waits for
  B; `A -->|parent-child| B` means A is a child of B; `A -->|discovered-from|
  B` means A was found while working B.
- Who creates a bead or edge:
  1. **poured** (`sc-compose bead attach`): purple
     dashed node; thick arrow `==>`. A pour makes only `parent-child` to the
     attach parent and `blocks` between its own steps.
  2. **relations** (the request's `relations`, from `scripts/bead-groups`): dotted arrow `-.->`, for
     `validates`, `discovered-from` and cross-sprint `blocks`.
  3. **template or script**: solid node and arrow `-->`; blue for a template
     plus `bd import`, green for a package script (the close script, the
     sanity hook and bd-cache are package scripts).
  - Yellow nodes are ATM tasks, messages and actions. Red nodes are
    refusals and escalations.
  - Files are parallelograms. A dashed arrow to or from a file is a lookup,
    not a bd edge.
- bd facts:
  - one dependency type per bead pair;
  - `validates` does not gate `bd ready`;
  - a parent with an open `blocks` dependency hides its children from
    `bd ready`;
  - `bd close` refuses a parent with open children.
- No assignment in advance: planned and poured beads carry `difficulty`
  (`hard`, `normal`, `fast`) and no assignee; the lead picks the agent at
  dispatch.
- Bead id = ATM task id.

## Open

- Verdict and round: required metadata on a closed qa bead, or read from its
  close reason.
- Classification of sprint container, finding and workflow-issue beads:
  `issue_type` (built-in or custom) or schema metadata.

## Not yet implemented

- Dev, fix and qa closes run `atm-task-close.py` by hand; only sanity has an
  ATM `task.close` hook. Moving them into the hook is planned.
- `bd ready`, the TUI and the plan tools read bd directly, so they see a
  journaled write only after the drain applies it (bd-cache.md).
