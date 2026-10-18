# atm-bd-orchestration

ATM + Beads orchestration skill pack, published as a Claude Code plugin
marketplace and as a synaptic-canvas style package for `sc-install`
(`.claude` and `.codex` targets).

```
.claude-plugin/marketplace.json          the marketplace manifest
plugins/
  atm-bd-orchestration/                  one package: skills + agents + installer
    .claude-plugin/plugin.json           Claude Code plugin manifest
    manifest.yaml                        sc-install package manifest (artifacts, requires)
    registry.yaml                        the files rendered at install time
    config/atm-bd-orchestration.yaml.j2  the configuration variables (required, no defaults)
    config/legacy-owned.json             hashes of files 0.x shipped, for the one-time migration
    install.py                           sc-install hook (prepare/complete/cleanup) and standalone installer
    conftest.py                          test path setup
    skills/{atm-beads,atm-bd-orchestration,qa-report,sprint-report,sprint-review}/
    agents/{dev-sanity,sc-sanity-llm,sc-sanity-jev}.md
    tests/                               package consistency and install tests
.github/workflows/tests.yml              the package tests on ubuntu and macos
```

## Use it

Claude Code marketplace:

```
/plugin marketplace add randlee/atm-bd-orchestration
/plugin install atm-bd-orchestration@atm-bd-orchestration
```

Then install the package into the repository where the agents run, because the
skills execute repository-relative scripts and dispatch templates:

```bash
python3 plugins/atm-bd-orchestration/install.py --dest /path/to/repo/.claude
python3 plugins/atm-bd-orchestration/install.py --dest /path/to/repo/.codex
```

`install.py` reads the configuration from the repository's
`.claude/agents/registry.yaml` (no defaults: a missing variable fails the install
and is named), renders it with `sc-compose --strict` into
`.claude/project/atm-bd-orchestration.yaml`, writes the `roles:` it resolved, and
records every file it placed in `.claude/project/atm-bd-orchestration.lock.json`
so reruns upgrade unmodified files and refuse modified or foreign ones.
Variables, checks and upgrades: [plugins/atm-bd-orchestration/README.md](plugins/atm-bd-orchestration/README.md).

synaptic-canvas will reference this repository's package with a `git-subdir`
marketplace entry and list it in its `sc-install` registry at release time;
nothing is copied.

## Develop

Use `atm-bd console --phase <x>` for the dashboard and `atm-bd cron --phase <x>`
for a single orchestration tick.

Console logging, delivery outcomes, and retention/rotation procedures are
documented in [apps/atm-bd-console/README.md](apps/atm-bd-console/README.md).

```bash
cd plugins/atm-bd-orchestration
uv run --with pytest --with pydantic --with pyyaml python -m pytest -q
python3 tests/gen_manifest.py   # after adding or removing a skill or agent file
```

No repository- or team-specific string may appear in `skills/` or `agents/`;
`tests/test_install.py` fails on the known ones. A value that differs per
repository becomes a required variable in `config/atm-bd-orchestration.yaml.j2`,
never an edit.
