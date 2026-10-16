# bh-1 Type Contract

The canonical frozen contract is the `atm-bh-1` bead design. Its declarations
are synchronized with the field orders below.

The positional field order in `types.py` is part of the current contract:

- `Sprint`: `id`, `title`, `layer`, `branch`, `target`, `stack`, `depends_on`, `closed`, `findings`.
- `Inputs`: `now`, `lead`, `repo`, `interval_s`, `phase`, `members`, `tasks`, `worktrees`, `remotes`, `merges`, `prs`, `gh`, `task_beads`.
- `PhaseFile`: `phase`, `root_id`, `label`, `sprints`, `depends_on`, `lead`, `templates`.

`Inputs.interval_s` is always `Config.interval_s`; the bh-9 runner builds
`Inputs` from `Config`. Do not add other `Config` mirrors to `Inputs`.

`RepoConfig.reviewers_round1` is part of the `RepoConfig` declaration in that
design and remains in its listed position before `plans_dir`.

The parallel sprint branches (bh-3 through bh-10) were rebased above the bh-1
contract layer and carry this field order. New fields must be supplied by
keyword in constructors from here on; existing fields and signatures are not
being changed except for the phase-settings update below.

## Phase settings (ANC023)

`PhaseFile.lead` is the validated nonempty phase TOML identity;
`Config.lead` is its resolved runtime value, refreshed each tick. Console
launches accept `--phase` and no longer accept `--as`. Neither environment
identity nor repository YAML supplies a fallback lead.

`PhaseFile.templates` maps exactly `dev`, `dev_fix`, `fix`, `sanity`, `qa`,
`plan_review`, and `review` to existing repository-relative files within
`.claude/skills/atm-bd-orchestration/templates/`. Missing or invalid lead and
template values return `Err(ConfigInvalid)` naming `lead` or
`templates.<purpose>` before launch effects.

`dispatch_spec(context, templates)` takes this validated mapping explicitly.
The runner binds it to the existing one-argument `SpecFn`; the pure model's
signature is unchanged. Only sanity and QA dispatch are supported, using the
matching purpose key and the existing repository-configured recipient.
`DispatchSpec.template` carries the exact configured relative path to ATM's
`--template` argument. New `PhaseFile` fields are supplied by keyword.
