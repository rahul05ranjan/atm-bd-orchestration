# atm-bd-orchestration

Bead-driven phase orchestration for ATM agent teams, as one installable package:

| Skill / agent | What it is |
| --- | --- |
| `skills/atm-beads` | the phase plan as a beads graph: plan templates, `validate-plan`, the pydantic bead schemas, `resolve-role`, the plan contract, the sprint index scripts |
| `skills/atm-bd-orchestration` | dispatch, dev-sanity, QA and stack landing driven by `bd ready`: assignment gates, dispatch and close templates, the `quality-mgr` role sheet, sanity run history, the phase-end post-mortem (with JEV screening) |
| `skills/sprint-report`, `skills/sprint-review` | sprint status tables and dependency DAGs from live beads; the sprint review command |
| `skills/prompt-rewrite-judge` | rewrite prompt text with fresh subagents and have Jev judge each rewrite for dropped, changed or unclear requirements (`scripts/judge.py`) |
| `skills/qa-report` | `/qa-report`: the two QA metrics logs quality-mgr appends under `.sc/qa-log/` (per-round events and cumulative phase stats), read-only |
| `agents/dev-sanity.md` | the single dev-sanity teammate: the whole dev-sanity role in one agent prompt |
| `agents/sc-sanity-llm.md`, `agents/sc-sanity-jev.md` | the per-deliverable LLM and Jev (typesafe.ai) subagents dev-sanity spawns |
| `agents/parallax.md` | optional work-orchestrator teammate: runs the lead's routine orchestration; the lead keeps `bv`, monitoring and rulings |
| `skills/atm-bd-orchestration/scripts/jev_client.py` | the Jev transport |

The skills run repository-relative scripts (`.claude/skills/<skill>/scripts/...`)
and dispatch templates that ATM agents execute inside the consuming repository,
so the package is installed **into a repository's `.claude/` (and optionally
`.codex/`) directory**, not used from the plugin cache. Marketplace and plugin
manifests exist so Claude Code can discover it; `install.py` puts it to work.

## Install

Requirements in the consuming repository:

- `.claude/agents/registry.yaml` with every configuration variable (below);
- `.beads/metadata.json` showing `"dolt_mode": "server"` (validate-plan needs
  `bd doctor --json`, which only server mode provides);
- `.claude/agents/<name>.md` for every agent named in `qa_member` and
  `reviewers_round1` (agents this package ships count). `dev_sanity_member`
  names a team member and is not checked: it may have no agent file;
- a git `origin` remote (`owner/name`);
- `sc-compose`, `atm`, `bd`, `jq`, `gh`, and `python3` with PyYAML and pydantic
  on PATH.

Jev sanity checks and post-mortem screening also need `TYPESAFE_API_KEY` in the
agent's environment at run time; without it `.claude/skills/atm-bd-orchestration/scripts/jev_client.py --startup`
reports `SANITY.JEV_UNAVAILABLE` and dev-sanity records every JEV slot as
unavailable (the LLM subagent still runs and selection takes its replies).

Standalone, from a checkout of this repository:

```bash
python3 plugins/atm-bd-orchestration/install.py --dest /path/to/repo/.claude
python3 plugins/atm-bd-orchestration/install.py --dest /path/to/repo/.codex   # skills only, no agents
python3 plugins/atm-bd-orchestration/install.py --dest /path/to/repo/.claude --set test_command="make test"
```

Through `sc-install` (synaptic-canvas): `sc-install install atm-bd-orchestration
--local` runs `install.py` as the package's `prepare()`/`complete()`/`cleanup()`
hook, with `--set` values in `options["args"]`. `prepare()` makes every check
below and writes nothing; `complete()` places, renders and records the files.

## Configuration

The variables are declared once, as the `required_variables` of
`config/atm-bd-orchestration.yaml.j2`. There are no defaults: install renders the
template strictly with the sc-compose Python wheel into
`<repo>/.claude/project/atm-bd-orchestration.yaml` and fails, naming every
missing variable and the registry key that sets it. Skills read that file at run
time (`skills/atm-beads/scripts/repo_config.py`).

| Variable | registry.yaml key | Example |
| --- | --- | --- |
| `bead_prefix` | `bead_prefix` | `myp` |
| `lead` | `roles.lead` | `team-lead` |
| `dev_sanity_member` | `roles.dev-sanity` | `dev-sanity` |
| `qa_member` | `roles.quality-mgr` | `quality-mgr` |
| `worktree_base` | `worktree_base` | `../my-repo-worktrees` |
| `test_command` | `test_command` | `just test` |
| `lint_command` | `lint_command` | `just lint` |
| `integration_branch_pattern` | `integration_branch_pattern` | `integrate/phase-{phase}` |
| `plans_dir` | `plans_dir` | `docs/plans` |
| `requirements_globs` | `requirements_globs` (list) | `[docs/requirements.md]` |
| `adr_globs` | `adr_globs` (list) | `["docs/adr/*.md"]` |
| `policy_path` | `policy_path` | `.claude/project/quality-policy.md` |
| `reviewers_round1` | `reviewers_round1` (list) | `[req-qa, arch-qa]` |

`--set NAME=VALUE` wins over registry.yaml; a list takes a JSON array or
`a,b,c`. An unknown name is an error. The resolved `lead`, `dev_sanity_member`
and `qa_member` are written back into `roles:` (the rest of registry.yaml is kept
as written), which is the map `resolve-role` reads.

The files listed under `render:` in `registry.yaml` (examples, a few docs and
tests) also carry install-time placeholders: `{{ lead }}`,
`{{ dev_sanity_member }}`, `{{ bead_prefix }}`, `{{ worktree_base }}` from the
configuration, and `{{ repo_slug }}`, `{{ repo_name }}` (git origin)
and `{{ workflow_issues_root }}` (`<bead_prefix>-workflow-issues`)
derived from the repository. Every other file, including every `*.j2` dispatch
template, is copied byte for byte. `sc-sanity-jev` and `post_mortem_jev.py` call
the Jev transport at `.claude/skills/atm-bd-orchestration/scripts/jev_client.py`.

## Ownership and upgrades

`<repo>/.claude/project/atm-bd-orchestration.lock.json` records the package
version and the sha256 of every file the install wrote (both targets, the Jev
transport and the config file). On every run:

- a recorded file that is unchanged since it was written is replaced by the new
  version's bytes; a recorded file that was modified fails the install, named;
- an unchanged recorded file the new version no longer ships is removed;
- an existing file the install did not record is never written over: the install
  fails, naming it, and fails once per target skill directory
  (`skills/atm-beads`, `skills/atm-bd-orchestration`, `skills/prompt-rewrite-judge`,
  `skills/qa-report`, `skills/sprint-report`, `skills/sprint-review`) that exists without any file it
  owns;
- a failed install writes nothing;
- `--overwrite` (sc-install: `overwrite` or `force`) turns each failing file
  above into a warning: the file is moved to `<repo>/.backup/<UTC time>/<path>`
  (git-ignored) and the package's file takes its place. A foreign skill
  directory still fails.

A pre-0.4.0 install has no lock file. The first 0.4.0 install adopts an existing
file when its bytes are what 0.4.0 installs or what any 0.x version shipped
(`config/legacy-owned.json`), and fails on the rest; see the migration note in
`CHANGELOG.md`.

## Tests

```bash
cd plugins/atm-bd-orchestration
uv run --with pytest --with pydantic --with pyyaml python -m pytest -q
python3 tests/gen_manifest.py --check   # manifest.yaml artifacts and the registry.yaml render list are generated
```

`conftest.py` sets the import path and keeps pytest out of `skills/` and
`agents/`: `tests/test_skill_suites.py` installs the package into a throwaway
repository (bead prefix `myp`) and runs the skills' own suites there, because
they resolve paths from the repository layout. One upstream test that reads the
source repository's own phase-d plan is deselected. The install tests need
`sc-compose` and are skipped, with a message, when it is not on PATH;
`tests/test_sc_compose_version.py` fails unless the sc-compose CLI and the
`python3` wheel are one version in the manifest's `sc-compose >= 1.7.0, < 2`
range (CI installs the floor);
`test_real_upgrade_from_a_0_2_3_install` needs the package history (a full
clone). CI (`.github/workflows/tests.yml`) runs all of them on ubuntu and macos.

## Provenance

Sources: sc-observability `develop` at f2ebe1bc plus open PR #933 at 07ad4d26
(`fix/jev-post-mortem-workflow`, the JEV post-mortem role and context workflow)
and open PR #966 at 05367233 (`fix/dev-sanity-triage`, dev-sanity selection, with
#967's filing-reviewer-only fix verification merged in),
with every repository- and team-specific string replaced by an install-time value
or a neutral example. Recheck the JEV files if #933 changes before it merges.
See `CHANGELOG.md`.

## Package-only changes pending upstream

Changes below are not yet in the upstream source. The next sync keeps each one
until upstream carries it, then drops it from this list.

- Upstream PR #936 at d566149d (unmerged), applied ahead of its merge
  (issue #8): `skills/atm-beads/templates/sprint-bead.json.j2` (required
  `difficulty` rendered into `metadata`, `stage:sprint` label) and
  `skills/atm-beads/examples/sprint-bead-vars-d-{4,5}.json` (`difficulty`).
  The package's version 0.3.1 also drops `assignee`.
- Fix verification is upstream for QA (sc-observability 18d7158f) and for plan
  review (PR #967, carried by #966 at 05367233): a fix is verified only by its
  filing reviewer, locked to the original finding, with no req-qa/arch-qa
  re-run, no ceremony screen and no new findings. Still package-only:
  - Plan finding lines name their filing reviewer,
    `<bead> <severity> <reviewer> <field>: ...` (`validate-plan` for step b):
    `skills/atm-bd-orchestration/templates/plan-review-template.xml.j2` step e,
    the plan-review bullets of `roles/quality-mgr.md`, and the reviewer column
    in `examples/plan-review-*-vars.json`.
  - `skills/atm-bd-orchestration/templates/plan-scope-reviewer-assignment.json.j2`
    (1.1.1, ported from atm-core 2676a514): `round_index` above 1 refuses to
    render without the reviewer's own ids (`FIX_ROUND_SCOPE_LOCK_REQUIRED`);
    `findings_scope_locked` flag; `plan-review-template.xml.j2` step c passes it
    `round_index` and those ids.
  - `skills/atm-bd-orchestration/scripts/fix-round-scope` (`owned`, `check`,
    `filter`; `check --dispatch`, and `--plan` for carried plan finding lines) and
    `scripts/tests/test_fix_round_scope.py`: the dispatch set of a fix
    verification is exactly the carried findings' filing reviewers, and their
    results reduce to dispositions on their own ids; `qa-template.xml.j2` step
    d and `plan-review-template.xml.j2` step c run it.
  - `skills/atm-bd-orchestration/templates/ruthless-boundary-qa-assignment.json.j2`
    (2.1.0): required `qa_round`; trimmed-scope `locked` flag; refuses
    `qa_round` above 1 without own ids.
  - `skills/atm-bd-orchestration/examples/ruthless-boundary-qa-assignment-vars.json`:
    `"qa_round": 2`.
