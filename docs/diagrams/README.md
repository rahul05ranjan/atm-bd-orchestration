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
user's request.

| File | Level | Shows |
| --- | --- | --- |
| [phase.md](phase.md) | phase | root, sprint containers, poured groups, cross-sprint edges, phase-wide beads, non-blocking findings |
| [sprint.md](sprint.md) | sprint | the sprint group, fix groups per blocking finding, a second fix round, sanity FAIL, closers |
| [lifecycle.md](lifecycle.md) | sprint | ready, claim and close order through the close hook; the layer linked by sanity under the stack bead lock; the dev task open until sanity PASS; where results and logs are written |
| [correlation.md](correlation.md) | phase | bead id equals ATM task id, `.atm-bd/<phase>.toml`, who holds each task |
| [formula.md](formula.md) | detail | pour stages, formula location and override, the beads and edges each formula pours, idempotent attach |

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
     plus `bd import`, green for a package script.
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

None: the package code matches these diagrams.
