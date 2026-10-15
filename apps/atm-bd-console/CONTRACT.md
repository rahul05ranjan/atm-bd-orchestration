# bh-1 Type Contract

The canonical frozen contract is the `atm-bh-1` bead design. Its declarations
are synchronized with the field orders below.

The positional field order in `types.py` is part of the current contract:

- `Sprint`: `id`, `title`, `layer`, `branch`, `target`, `stack`, `depends_on`, `closed`, `findings`.
- `Inputs`: `now`, `lead`, `repo`, `interval_s`, `phase`, `members`, `tasks`, `worktrees`, `remotes`, `merges`, `prs`, `gh`, `task_beads`.

`RepoConfig.reviewers_round1` is part of the `RepoConfig` declaration in that
design and remains in its listed position before `plans_dir`.

The parallel sprint branches (bh-3 through bh-10) were rebased above the bh-1
contract layer and carry this field order. New fields must be supplied by
keyword in constructors from here on; existing fields and signatures are not
being changed.
