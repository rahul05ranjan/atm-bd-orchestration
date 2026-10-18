# PyPI chain provenance

Source: `randlee/atm-core` checkout at
`997e7ddda4e5d572249e5aefeb208b2a67aec79d` (read-only during this port).
Task: `atm-phase-bi-review-f5`. No workflow was dispatched.

## Byte-identical copies

- `.github/workflows/pypi-publish.yml`
- `.github/actions/verify-published-release/action.yml`
- `.github/scripts/release_artifacts.py`
- `.github/scripts/release_manifest.py`
- `.github/scripts/release_python.py`
- `.github/scripts/release_registry.py`
- `.github/scripts/release_immutability.py`
- `.github/scripts/npm_release.py` (imported unconditionally by manifest validation;
  no npm workflow or package is enabled)
- `.github/scripts/worker_result.py` and its complete `tests/test_worker_result.py`
- `.claude/agents/pypi-publisher.md`
- `.claude/agents/publisher-channel-protocol.md`
- `.claude/skills/publishing/ref/channel-contracts.md`
- `release/publish-channel-contracts.toml`

## Non-verbatim files

`.github/workflows/release.yml` is the minimal Python/GitHub subset authorized
by lead ruling `01M57ZZXS99S9ZKMTDZ8EWTT4T`, not the full multi-channel workflow:

- Dispatch accepts an existing `tag`, rather than creating tags from `version`
  and selecting a root publication target. Checkout uses that tag. The plain-git
  main-ancestry check is retained; checkout identity and version are checked.
- Cargo, npm, registry publication, candidate-tag policy, Rust toolchains, matrices,
  and the calls to `release_gate.sh`/`npm_release.py` are omitted.
- The upstream `python -m build` wheel/sdist steps use the literal consumer
  source path `apps/atm-bd-console`; one Linux build suffices for its pure-Python wheel.
- The upstream artifact-upload steps become one `python-distributions` artifact
  containing both outputs. The release job depends only on this Python build.
- The release job is otherwise extracted from upstream: immutable-state check,
  fail-closed published-release probe, skip-existing-release handling, collection,
  metadata verification, checksums, release upload, and finalized immutability check.
  References to gate outputs become `inputs.tag`; replacement conditions and their
  log step are omitted. Expected assets add the consumer wheel/sdist filename
  patterns alongside checksums. Unrelated upstream comments are omitted.
- Concurrency uses the input tag. GitHub permissions remain `contents: write`.

`release/publish-artifacts.toml` contains only atm-bd project/package paths,
one wheel and one sdist, and the PyPI channel. The shared validator requires
a release-target table even when no binary archive is declared, so a Linux
target is present but no binary build is enabled. Lead ruling
`01M5800GESYYV9PJV7JDW4GQ21` preserves `build_system = "setuptools"` as the
upstream generic PEP517 label: `python -m build` still invokes the real hatchling
backend declared by the consumer's pyproject. Its explanatory manifest comment
is the only new inline comment requested by that ruling.

`tests/test_release_artifacts.py` retains the applicable upstream fixture/helper
functions and Python manifest/matrix/uploader/version/asset tests. Other channel,
renderer, installer, Cargo-workflow and publish-kit-installation tests are omitted.
Its only changed retained lines are path adaptations: `repo_root()` uses
`Path(__file__).resolve().parents[3]`, and the fixture reads the copied
`publish-channel-contracts.toml`, not an uninstalled `.toml.j2` template.

`tests/test_release_immutability.py` retains upstream behavioral tests unchanged;
only the full-kit `INSTALL` import and its non-applicable workflow/preflight test
are omitted. `tests/test_release_python.py` retains upstream invalid-wheel and
legacy-runner tests and their target fixture; installer-dependent cases/imports
are omitted. No sleeping-child deadline test is copied.

New `tests/test_pypi_workflows.py` verifies the consumer wiring and executes the
copied release verifier against deterministic local `gh` responses. The existing
CI test workflow gains a nonpublishing release-tooling test step.

## Consumer changes

The bespoke `publish.yml` and `test_publish_workflow.py` are removed. Current
version pins in pyproject, lockfile, `__init__`, and skill-output assertions move
from 0.17.1 to 0.17.2. Historical changelog entries are unchanged.

The publisher workflow, helpers, agent and contracts are not installed via
sc-publish and have no repository-specific logic edits. Full per-file unified
diffs against the source originals are in [upstream-differences.diff](upstream-differences.diff);
byte-identical files have empty diffs.

An additional diagnostic of the unchanged, unused `verify-version-lockstep`
command exposed an upstream nested-workspace path assumption: it joins the
root-relative Python package manifest to the nested version source's parent,
doubling `apps/atm-bd-console`. The minimal workflow does not invoke that
command. Its actual `verify-version`, `validate-manifest`, build-plan, and
real wheel/sdist verification commands pass. The diagnostic was reported to
the lead; the copied helper was not modified to hide it.
