# bh-1 Type Contract

The positional field order in `types.py` is part of the current contract:

- `Sprint`: `id`, `title`, `layer`, `branch`, `target`, `stack`, `depends_on`, `closed`, `findings`.
- `Inputs`: `now`, `lead`, `repo`, `interval_s`, `phase`, `members`, `tasks`, `worktrees`, `remotes`, `merges`, `prs`, `gh`, `task_beads`.

The parallel sprint branches (bh-3 through bh-10) were rebased above the bh-1
contract layer and carry this field order. New fields must be supplied by
keyword in constructors from here on; existing fields and signatures are not
being changed.
