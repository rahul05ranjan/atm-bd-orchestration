"""atm_bd tests (bh-4 owned path).

D2: phase_file and repo_config on copies of atm-core's real phase-bh files.

tests/fixtures/beads/repo/ adapts atm-core's `.atm-bd/phase-bh.toml` with phase settings,
`docs/plans/phase-bh.jsonl` and `.claude/project/atm-bd-orchestration.yaml` (2026-10-14).

D3: phase() over recorded `bd --json` output of real phase-bh beads (atm-core, 2026-10-14).

Fixtures in tests/fixtures/beads/, captured read-only from atm-core:
- list-open.json: `bd list -l phase-bh --json`, kept to the root, the plan's open sprint containers and
  group beads, the findings atm-bh-4.group-dev.3/.4 and atm-bh-6.group-qa-f1, and the QA bead atm-bh-6.group-qa-f1-qa.
- ready.json: `bd ready -l phase-bh --json`, kept to the same ids.
- show-closed.json: `bd show <the 12 plan-derived ids absent from the open list> --json`, verbatim.
The fake `bd` below only replays those files; beads_source and atm_bd run unchanged.

D5: dispatch_spec fills the production templates exactly; each spec renders with sc-compose --strict.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import get_args

import pytest
from contract_samples import PHASE_TEMPLATES
from process_helpers import run_process

from atm_bd_console import beads_source
from atm_bd_console.atm_bd import (
    DISPATCH_KEYS,
    OTHER_KIND,
    QA_KIND,
    _kind,
    closed_show_ids,
    dispatch_spec,
    group_ids,
    group_suffix,
    phase,
    phase_file,
    repo_config,
    step,
)
from atm_bd_console.enums import CiState, SprintState
from atm_bd_console.result import ConfigInvalid, Err, IoFailed, NotFound, Ok, ParseFailed
from atm_bd_console.types import (
    Bead,
    DispatchContext,
    Phase,
    PhaseFile,
    PullRequest,
    RepoConfig,
    Sprint,
    Step,
    StepKind,
)

# D2: phase_file and repo_config.
REPO = Path(__file__).parent / "fixtures" / "beads" / "repo"
TOML = ".atm-bd/phase-bh.toml"
PLAN = "docs/plans/phase-bh.jsonl"
CONFIG = ".claude/project/atm-bd-orchestration.yaml"

# Hand-mapped from docs/plans/phase-bh.jsonl: sprint <name> -> container atm-<name> (prefix of root atm-phase-bh).
EXPECTED_PHASE_FILE = PhaseFile(
    "bh", "atm-phase-bh", "phase-bh",
    tuple(f"atm-bh-{n}" for n in range(1, 11)),
    {
        "atm-bh-1": (), "atm-bh-2": (),
        **{f"atm-bh-{n}": ("atm-bh-1",) for n in range(3, 9)},
        "atm-bh-9": ("atm-bh-3", "atm-bh-4", "atm-bh-5", "atm-bh-6", "atm-bh-7", "atm-bh-8"),
        "atm-bh-10": ("atm-bh-9",),
    },
    lead="fenix", templates=PHASE_TEMPLATES,
)
REVIEWERS = (
    "req-qa", "arch-qa", "ruthless-boundary-qa", "rust-qa-agent", "rust-best-practices-agent",
    "rust-service-hardening-agent",
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(REPO, root)
    return root


def _err(result: object) -> object:
    assert isinstance(result, Err), result
    assert result.error.fix, result.error
    return result.error


def test_phase_file_reads_the_real_toml_and_its_plan() -> None:
    assert phase_file(str(REPO), "bh") == Ok(EXPECTED_PHASE_FILE)


def test_phase_file_root_comes_from_the_toml_and_sprints_from_the_plan(repo: Path) -> None:
    toml = repo / TOML
    toml.write_text(toml.read_text().replace('"atm-phase-bh"', '"zz-phase-bh"'))
    (repo / PLAN).write_text('{"sprint": "bh-1"}\n\n{"sprint": "bh-2", "depends_on": ["bh-1"]}\n')
    result = phase_file(str(repo), "bh")
    assert result == Ok(PhaseFile("bh", "zz-phase-bh", "phase-bh", ("zz-bh-1", "zz-bh-2"),
                                  {"zz-bh-1": (), "zz-bh-2": ("zz-bh-1",)}, lead="fenix", templates=PHASE_TEMPLATES))


def test_phase_file_plan_path_is_the_toml_plan_key(repo: Path) -> None:
    (repo / "elsewhere.jsonl").write_text('{"sprint": "only"}\n')
    toml = repo / TOML
    toml.write_text(toml.read_text().replace(PLAN, "elsewhere.jsonl"))
    result = phase_file(str(repo), "bh")
    assert isinstance(result, Ok)
    assert result.value.sprints == ("atm-only",)


def test_missing_phase_file_is_not_found(tmp_path: Path) -> None:
    error = _err(phase_file(str(tmp_path), "bh"))
    assert isinstance(error, NotFound)
    assert error.what == str(tmp_path / TOML)


def test_invalid_phase_id_is_config_invalid() -> None:
    error = _err(phase_file(str(REPO), "../bh"))
    assert isinstance(error, ConfigInvalid)
    assert (error.key, error.value) == ("phase", "../bh")


def test_malformed_toml_is_parse_failed(repo: Path) -> None:
    (repo / TOML).write_text("plan = \n")
    error = _err(phase_file(str(repo), "bh"))
    assert isinstance(error, ParseFailed)
    assert error.source == str(repo / TOML)


def test_unreadable_phase_file_is_io_failed(repo: Path) -> None:
    (repo / TOML).unlink()
    (repo / TOML).mkdir()
    error = _err(phase_file(str(repo), "bh"))
    assert isinstance(error, IoFailed)
    assert error.path == str(repo / TOML)


@pytest.mark.parametrize("key", ["plan", "root", "integration_branch"])
def test_each_missing_phase_key_is_not_found_naming_it(repo: Path, key: str) -> None:
    toml = repo / TOML
    toml.write_text("".join(line + "\n" for line in toml.read_text().splitlines() if not line.startswith(f"{key} = ")))
    error = _err(phase_file(str(repo), "bh"))
    assert isinstance(error, NotFound)
    assert error.what == f"{repo / TOML}: key {key}"


def test_empty_phase_key_is_config_invalid(repo: Path) -> None:
    toml = repo / TOML
    toml.write_text(toml.read_text().replace('"integrate/phase-bh"', '""'))
    error = _err(phase_file(str(repo), "bh"))
    assert isinstance(error, ConfigInvalid)
    assert error.key == "integration_branch"


def test_root_of_another_phase_is_config_invalid(repo: Path) -> None:
    toml = repo / TOML
    toml.write_text(toml.read_text().replace('"atm-phase-bh"', '"atm-phase-bg"'))
    error = _err(phase_file(str(repo), "bh"))
    assert isinstance(error, ConfigInvalid)
    assert (error.key, error.value) == ("root", "atm-phase-bg")


def test_missing_plan_file_is_not_found_naming_it(repo: Path) -> None:
    (repo / PLAN).unlink()
    error = _err(phase_file(str(repo), "bh"))
    assert isinstance(error, NotFound)
    assert error.what == str(repo / PLAN)


def test_unreadable_plan_file_is_io_failed(repo: Path) -> None:
    (repo / PLAN).unlink()
    (repo / PLAN).mkdir()
    error = _err(phase_file(str(repo), "bh"))
    assert isinstance(error, IoFailed)
    assert error.path == str(repo / PLAN)


@pytest.mark.parametrize(
    ("plan", "source_suffix", "message"),
    [
        ('{"sprint": "bh-1"}\nnot json\n', ":2", "Expecting value"),
        ('{"name": "bh-1"}\n', ":1", "not a plan row"),
        ('{"sprint": "bh-1", "extra": 1}\n', ":1", "not a plan row"),
        ('{"sprint": "BH 1"}\n', ":1", "is not a lowercase name"),
        ('{"sprint": "bh-1", "depends_on": "bh-0"}\n', ":1", "depends_on is not a list"),
        ('{"sprint": "bh-1"}\n{"sprint": "bh-1"}\n', ":2", "listed twice"),
        ("\n", "", "the plan lists no sprints"),
        ('{"sprint": "bh-2", "depends_on": ["bh-9"]}\n', "", "bh-2 depends_on unknown sprint(s): bh-9"),
    ],
)
def test_bad_plan_rows_are_parse_failed(repo: Path, plan: str, source_suffix: str, message: str) -> None:
    (repo / PLAN).write_text(plan)
    error = _err(phase_file(str(repo), "bh"))
    assert isinstance(error, ParseFailed)
    assert error.source == f"{repo / PLAN}{source_suffix}"
    assert message in error.message


def test_repo_config_on_the_real_file_is_ok_and_primary_checkout_is_repo_root() -> None:
    # Plan-owner ruling (atm-bh-4): primary_checkout is the repo_root passed in, never read from the yaml.
    assert repo_config(str(REPO)) == Ok(RepoConfig(
        "atm", "atm-sanity", "quality-mgr", str(REPO), "../atm-core-worktrees", "just lint",
        ".claude/project/quality-policy.md", REVIEWERS, "docs/plans",
    ))


def test_repo_config_ignores_a_primary_checkout_key_in_the_file(repo: Path) -> None:
    config = repo / CONFIG
    config.write_text(config.read_text() + 'primary_checkout: "/elsewhere"\n')
    result = repo_config(str(repo))
    assert isinstance(result, Ok)
    assert result.value.primary_checkout == str(repo)


def test_repo_config_without_qa_member_is_not_found_naming_it(repo: Path) -> None:
    config = repo / CONFIG
    config.write_text("".join(line + "\n" for line in config.read_text().splitlines()
                              if not line.startswith("qa_member:")))
    error = _err(repo_config(str(repo)))
    assert isinstance(error, NotFound)
    assert error.what == f"{config}: key qa_member"


def test_repo_config_reads_a_block_reviewer_list(repo: Path) -> None:
    config = repo / CONFIG
    lines = [line for line in config.read_text().splitlines() if not line.startswith("reviewers_round1:")]
    config.write_text("\n".join([*lines, "reviewers_round1:", "  - req-qa", '  - "arch-qa"']) + "\n")
    result = repo_config(str(repo))
    assert isinstance(result, Ok)
    assert result.value.reviewers_round1 == ("req-qa", "arch-qa")


def test_repo_config_reviewers_not_a_list_is_config_invalid(repo: Path) -> None:
    config = repo / CONFIG
    lines = [line for line in config.read_text().splitlines() if not line.startswith("reviewers_round1:")]
    config.write_text("\n".join([*lines, 'reviewers_round1: "req-qa"']) + "\n")
    error = _err(repo_config(str(repo)))
    assert isinstance(error, ConfigInvalid)
    assert error.key == "reviewers_round1"


def test_repo_config_missing_file_is_not_found(tmp_path: Path) -> None:
    error = _err(repo_config(str(tmp_path)))
    assert isinstance(error, NotFound)
    assert error.what == str(tmp_path / CONFIG)


# D3: phase() over recorded bd output.
FIXTURES = Path(__file__).parent / "fixtures" / "beads"
PLAN_FILE = phase_file(str(FIXTURES / "repo"), "bh")
# Hand-derived from docs/plans/phase-bh.jsonl: each container and its group-{dev,sanity,qa}, minus the open ones.
CLOSED_IDS = (
    "atm-bh-1.group-dev", "atm-bh-1.group-sanity", "atm-bh-1.group-qa", "atm-bh-2.group-dev",
    "atm-bh-3.group-dev", "atm-bh-3.group-sanity", "atm-bh-3.group-qa", "atm-bh-5.group-dev",
    "atm-bh-6.group-dev", "atm-bh-6.group-sanity", "atm-bh-6.group-qa", "atm-bh-7.group-dev",
)
PLAN_DEPENDS_ON = {
    "atm-bh-1": (), "atm-bh-2": (), **{f"atm-bh-{n}": ("atm-bh-1",) for n in range(3, 9)},
    "atm-bh-9": ("atm-bh-3", "atm-bh-4", "atm-bh-5", "atm-bh-6", "atm-bh-7", "atm-bh-8"), "atm-bh-10": ("atm-bh-9",),
}


def _replay_bd(fake_bin) -> None:
    fake_bin.make("bd", (
        'case "$1" in\n'
        f'  list) cat "{FIXTURES / "list-open.json"}" ;;\n'
        f'  ready) cat "{FIXTURES / "ready.json"}" ;;\n'
        f'  show) cat "{FIXTURES / "show-closed.json"}" ;;\n'
        "  *) exit 9 ;;\n"
        "esac\n"
    ))


def _ok(result):
    assert isinstance(result, Ok), result
    return result.value


def _read(fake_bin) -> tuple[tuple[Bead, ...], tuple[Bead, ...], tuple[Bead, ...]]:
    """One tick's bead reads: open and ready by label, then one batched show of the plan-derived closed ids."""
    _replay_bd(fake_bin)
    file = _ok(PLAN_FILE)
    open_beads = _ok(beads_source.open_beads(file.label))
    ready = _ok(beads_source.ready(file.label))
    shown = _ok(beads_source.show(closed_show_ids(file, open_beads + ready)))
    return open_beads, ready, shown


def _root(beads: tuple[Bead, ...]) -> Bead:
    return next(bead for bead in beads if bead.id == "atm-phase-bh")


def _phase(open_beads, ready, shown) -> Phase:
    return _ok(phase(_ok(PLAN_FILE), _root(open_beads), open_beads, ready, shown))


def test_closed_ids_are_derived_from_the_plan_and_shown_in_one_batched_call(fake_bin) -> None:
    open_beads, ready, shown = _read(fake_bin)
    assert closed_show_ids(_ok(PLAN_FILE), open_beads + ready) == CLOSED_IDS
    assert fake_bin.argv() == [
        ("bd", "list", "-l", "phase-bh", "--json"),
        ("bd", "ready", "-l", "phase-bh", "--json"),
        ("bd", "show", *CLOSED_IDS, "--json"),
    ]
    # The recording was made with the ids in sorted order; bd returns them in request order.
    assert sorted(bead.id for bead in shown) == sorted(CLOSED_IDS)
    assert {bead.status for bead in shown} == {SprintState.CLOSED}


def test_closed_ids_cover_every_container_and_group_when_nothing_is_open() -> None:
    file = _ok(PLAN_FILE)
    ids = closed_show_ids(file, ())
    assert len(ids) == 40
    assert ids[:4] == ("atm-bh-1", "atm-bh-1.group-dev", "atm-bh-1.group-sanity", "atm-bh-1.group-qa")
    assert group_ids("atm-bh-10") == ("atm-bh-10.group-dev", "atm-bh-10.group-sanity", "atm-bh-10.group-qa")


def test_unknown_bead_status_survives_into_sprint_problem_evidence(fake_bin) -> None:
    open_beads, ready, shown = _read(fake_bin)
    updated = tuple(replace(bead, status=SprintState.UNKNOWN, raw_status="wat") if bead.id == "atm-bh-1" else bead
                    for bead in open_beads)
    updated_ready = tuple(replace(bead, status=SprintState.UNKNOWN, raw_status="wat") if bead.id == "atm-bh-1" else bead
                          for bead in ready)
    result = _phase(updated, updated_ready, shown)
    sprint = next(item for item in result.sprints if item.id == "atm-bh-1")
    assert sprint.status is SprintState.UNKNOWN
    assert sprint.raw_status == "wat"


def test_phase_bh_yields_ten_sprints_from_their_bead_metadata_and_the_plan(fake_bin) -> None:
    open_beads, ready, shown = _read(fake_bin)
    result = _phase(open_beads, ready, shown)
    raw = {item["id"]: item for item in json.loads((FIXTURES / "list-open.json").read_text())}
    assert [sprint.id for sprint in result.sprints] == [f"atm-bh-{n}" for n in range(1, 11)]
    for sprint in result.sprints:
        meta = raw[sprint.id]["metadata"]
        assert (sprint.layer, sprint.branch, sprint.target, sprint.stack) == (
            meta["layer"], meta["branch"], meta["pr_target"], meta["stack"])
        assert sprint.depends_on == PLAN_DEPENDS_ON[sprint.id]
        assert sprint.title == raw[sprint.id]["title"]
        assert sprint.closed is False
    bh4 = next(sprint for sprint in result.sprints if sprint.id == "atm-bh-4")
    assert (bh4.layer, bh4.branch, bh4.target) == (4, "sprint/bh-4-beads-source", "sprint/bh-1-contract")
    assert result.step_errors == ()
    assert (result.root_id, result.label, result.closed) == ("atm-phase-bh", "phase-bh", False)


def test_findings_count_the_sprints_finding_beads(fake_bin) -> None:
    result = _phase(*_read(fake_bin))
    findings = {sprint.id: sprint.findings for sprint in result.sprints}
    # atm-bh-4.group-dev.3/.4 hang under atm-bh-4.group-dev; atm-bh-6.group-qa-f1 is under the root with sprint_bead.
    assert findings == {**{f"atm-bh-{n}": 0 for n in range(1, 11)}, "atm-bh-4": 2, "atm-bh-6": 1}


def test_shown_closed_groups_are_steps_and_ready_marks_steps(fake_bin) -> None:
    result = _phase(*_read(fake_bin))
    steps = {step.bead_id: step for step in result.steps}
    for bead_id in CLOSED_IDS:
        assert steps[bead_id].status is SprintState.CLOSED
    assert steps["atm-bh-1.group-qa"].kind == "qa"
    assert steps["atm-bh-8.group-dev"].ready is True
    assert steps["atm-bh-4.group-qa"].ready is False
    assert not {"atm-phase-bh", *(f"atm-bh-{n}" for n in range(1, 11))} & set(steps)


def test_without_the_batched_show_closed_groups_are_absent(fake_bin) -> None:
    open_beads, ready, _ = _read(fake_bin)
    steps = {step.bead_id for step in _phase(open_beads, ready, ()).steps}
    assert not set(CLOSED_IDS) & steps


def test_planted_missing_sprint_is_reported_not_dropped(fake_bin) -> None:
    open_beads, ready, shown = _read(fake_bin)
    gone = "atm-bh-5"
    result = _phase(tuple(b for b in open_beads if b.id != gone), tuple(b for b in ready if b.id != gone), shown)
    assert [sprint.id for sprint in result.sprints] == [f"atm-bh-{n}" for n in range(1, 11) if n != 5]
    assert [bead_id for bead_id, _ in result.step_errors] == [gone]
    error = result.step_errors[0][1]
    assert isinstance(error, NotFound)
    assert error.what == f"sprint bead {gone} listed in the plan"
    assert "closed_show_ids" in error.fix


def test_planted_closed_sprint_comes_from_the_batched_show(fake_bin) -> None:
    open_beads, ready, shown = _read(fake_bin)
    bh2 = next(bead for bead in open_beads if bead.id == "atm-bh-2")
    open_beads = tuple(bead for bead in open_beads if bead.id != "atm-bh-2")
    ready = tuple(bead for bead in ready if bead.id != "atm-bh-2")
    assert "atm-bh-2" in closed_show_ids(_ok(PLAN_FILE), open_beads + ready)
    result = _phase(open_beads, ready, (*shown, replace(bh2, status=SprintState.CLOSED)))
    bh2_sprint = next(sprint for sprint in result.sprints if sprint.id == "atm-bh-2")
    assert bh2_sprint.closed is True
    assert bh2_sprint.layer == 2
    assert result.step_errors == ()


def test_planted_sprint_without_layer_metadata_is_a_step_error(fake_bin) -> None:
    open_beads, ready, shown = _read(fake_bin)

    def strip(bead: Bead) -> Bead:
        return replace(bead, metadata={k: v for k, v in bead.metadata.items() if k != "layer"}) \
            if bead.id == "atm-bh-3" else bead
    result = _phase(tuple(map(strip, open_beads)), tuple(map(strip, ready)), shown)
    assert "atm-bh-3" not in {sprint.id for sprint in result.sprints}
    assert len(result.sprints) == 9
    assert [(bead_id, error.what) for bead_id, error in result.step_errors] == [
        ("atm-bh-3", "atm-bh-3.metadata.layer")]


def test_unusable_root_is_err(fake_bin) -> None:
    open_beads, ready, shown = _read(fake_bin)
    file = _ok(PLAN_FILE)
    missing = phase(file, None, open_beads, ready, shown)
    assert isinstance(missing, Err) and isinstance(missing.error, NotFound)
    assert missing.error.what == "atm-phase-bh (no root bead)"
    wrong = phase(file, next(b for b in open_beads if b.id == "atm-bh-4"), open_beads, ready, shown)
    assert isinstance(wrong, Err) and isinstance(wrong.error, NotFound)
    assert wrong.error.what == "atm-phase-bh (root bead atm-bh-4)"
    unlabelled = replace(_root(open_beads), labels=("scope:epic",))
    mislabelled = phase(file, unlabelled, open_beads, ready, shown)
    assert isinstance(mislabelled, Err) and isinstance(mislabelled.error, ConfigInvalid)
    assert mislabelled.error.key == "atm-phase-bh.labels"
    for result in (missing, wrong, mislabelled):
        assert result.error.fix


def _bead(bead_id: str, parent: str | None, labels: tuple[str, ...] = (), metadata: dict | None = None) -> Bead:
    from datetime import UTC, datetime

    return Bead(bead_id, bead_id, SprintState.OPEN, labels, parent, (), None, metadata or {}, datetime(2026, 10, 15, tzinfo=UTC), None, None)


def test_step_maps_a_dev_bead_with_its_top_epic_and_tag() -> None:
    from atm_bd_console.atm_bd import step

    root = _bead("atm-phase-bh", None, ("scope:epic",))
    sprint = _bead("atm-bh-4", "atm-phase-bh", ("stage:sprint",))
    dev = _bead("atm-bh-4.group-dev", "atm-bh-4", ("stage:dev",), {"sprint": "bh-4", "branch": "b", "pr_target": "t", "layer": 4, "difficulty": "normal"})
    result = step(dev, {root.id: root, sprint.id: sprint})
    assert isinstance(result, Ok)
    assert (result.value.kind, result.value.top_epic, result.value.tag) == ("dev", "atm-phase-bh", "phase-bh")
    assert (result.value.branch, result.value.target, result.value.layer, result.value.difficulty) == ("b", "t", 4, "normal")


@pytest.mark.parametrize("suffix,kind,metadata", (
    ("dev", "dev", {}),
    ("sanity", "sanity", {"dev_bead": "atm-bh-4.group-dev"}),
    ("qa", "qa", {"checked_bead": "atm-bh-4.group-dev", "round": 1, "sprint_bead": "atm-bh-4"}),
))
def test_step_maps_group_suffixes_without_stage_labels(suffix, kind, metadata) -> None:
    from atm_bd_console.atm_bd import step

    bead = _bead(f"atm-bh-4.group-{suffix}", "atm-bh-4", ("phase:bh",), metadata)
    result = step(bead, {})

    assert isinstance(result, Ok) and result.value.kind == kind


def test_step_top_epic_is_none_when_the_chain_is_broken_anywhere() -> None:
    from atm_bd_console.atm_bd import step

    sprint = _bead("atm-bh-4", "atm-phase-bh", ("stage:sprint",))
    dev = _bead("atm-bh-4.group-dev", "atm-bh-4", ("stage:dev",), {"sprint": "bh-4"})
    partway = step(dev, {sprint.id: sprint})  # parent present, grandparent referenced but missing
    assert isinstance(partway, Ok) and partway.value.top_epic is None
    first = step(dev, {})  # parent itself missing
    assert isinstance(first, Ok) and first.value.top_epic is None
    orphan = step(_bead("atm-phase-bh", None), {})
    assert isinstance(orphan, Ok) and orphan.value.top_epic is None and orphan.value.tag == "phase-bh"


def test_step_sanity_and_qa_keys_and_err_not_found() -> None:
    from atm_bd_console.atm_bd import step

    sanity = _bead("atm-bh-4.group-sanity", "atm-bh-4", ("stage:dev-sanity",), {"dev_bead": "atm-bh-4.group-dev", "sprint": "bh-4"})
    assert step(sanity, {}).value.checked_bead == "atm-bh-4.group-dev"
    qa = _bead("atm-bh-4.group-qa", "atm-bh-4", ("stage:qa",), {"checked_bead": "atm-bh-4.group-dev", "round": 1, "sprint_bead": "atm-bh-4", "sprint": "bh-4"})
    got = step(qa, {}).value
    assert (got.kind, got.checked_bead, got.qa_round, got.sprint_bead, got.carry_forward) == ("qa", "atm-bh-4.group-dev", 1, "atm-bh-4", None)
    closed_qa = replace(qa, close_reason="PASS: 0 findings filed")
    assert step(closed_qa, {}).value.close_reason == "PASS: 0 findings filed"
    fix_qa = _bead("atm-bh-4.f1-qa", "atm-bh-4", ("stage:qa",), {"checked_bead": "atm-bh-4.f1", "round": 2, "sprint_bead": "atm-bh-4", "sprint": "bh-4"})
    fix = _bead("atm-bh-4.f1", "atm-bh-4", ("stage:fix",), {"round": 1})
    fixed = step(fix_qa, {fix.id: fix}).value
    assert (fixed.carry_forward, fixed.qa_round) == ("atm-bh-4.f1", 2)
    no_checked = step(_bead("atm-bh-4.group-sanity", "atm-bh-4", ("stage:dev-sanity",)), {})
    assert isinstance(no_checked, Err) and isinstance(no_checked.error, NotFound) and "atm-bh-4.group-sanity.metadata.dev_bead" in no_checked.error.what
    no_round = step(_bead("atm-bh-4.group-qa", "atm-bh-4", ("stage:qa",), {"checked_bead": "x", "sprint_bead": "atm-bh-4"}), {})
    assert isinstance(no_round, Err) and "metadata.round" in no_round.error.what
    no_sprint = step(_bead("atm-bh-4.group-qa", "atm-bh-4", ("stage:qa",), {"checked_bead": "x", "round": 1}), {})
    assert isinstance(no_sprint, Err) and "metadata.sprint_bead" in no_sprint.error.what


def test_legacy_qa_resolves_sprint_from_its_container_when_checked_bead_is_not_loaded() -> None:
    from atm_bd_console.atm_bd import step

    root = _bead("atm-phase-bi", None, ("phase-bi",))
    sprint = _bead("atm-bi-1", root.id, ("stage:sprint",), {"sprint": "bi-1"})
    qa = _bead(
        "atm-bi-1.group-qa-f2-qa", sprint.id, ("stage:qa",),
        {"checked_bead": "atm-bi-1.group-qa-f2", "round": 1, "sprint": "bi-1", "phase": "bi"},
    )

    result = step(qa, {root.id: root, sprint.id: sprint, qa.id: qa})

    assert isinstance(result, Ok)
    assert result.value.sprint_bead == sprint.id


def test_phase_keeps_legacy_qa_step_when_checked_bead_is_not_loaded() -> None:
    from atm_bd_console.atm_bd import phase
    from atm_bd_console.types import PhaseFile

    root = _bead("atm-phase-bi", None, ("phase-bi",))
    sprint = _bead(
        "atm-bi-1", root.id, ("stage:sprint",),
        {"sprint": "bi-1", "layer": 1, "branch": "sprint/bi-1", "pr_target": "main", "stack": "phase-bi"},
    )
    qa = _bead(
        "atm-bi-1.group-qa-f2-qa", sprint.id, ("stage:qa",),
        {"checked_bead": "atm-bi-1.group-qa-f2", "round": 1, "sprint": "bi-1", "phase": "bi"},
    )
    file = PhaseFile("bi", root.id, "phase-bi", (sprint.id,), {}, "lead", {})

    result = phase(file, root, (sprint, qa), (qa,), ())

    assert isinstance(result, Ok)
    assert result.value.step_errors == ()
    mapped = next(item for item in result.value.steps if item.bead_id == qa.id)
    assert (mapped.sprint_bead, mapped.ready) == (sprint.id, True)


# D4/D5: step mapping and dispatch_spec.
TEMPLATES = Path(__file__).resolve().parents[3] / "plugins/atm-bd-orchestration/skills/atm-bd-orchestration/templates"
NOW = datetime(2026, 10, 15, 4, 0, tzinfo=UTC)
DISPATCH_REPO = RepoConfig("atm", "dev-sanity", "quality-mgr", "/repo", "/worktrees", "just lint", ".claude/project/quality-policy.md",
                  ("req-qa", "arch-qa"), "docs/plans")
SPRINT = Sprint("atm-bh-5", "git and GitHub sources", 5, "sprint/bh-5-git-github-source", "sprint/bh-1-contract",
                "phase-bh", ("atm-bh-1",), False, 2)
PR = PullRequest(148, "https://github.com/randlee/atm-bd-orchestration/pull/148", "fix/bh-5-qa-r1",
                 "sprint/bh-10-textual-tabs", "77543fe", CiState.RUNNING, ())
LAYER_PRS = (PullRequest(140, "https://github.com/randlee/atm-bd-orchestration/pull/140",
                          "sprint/bh-5-git-github-source", "fix/bh-1-qa-r1", "235f3a7", CiState.PASS, ()),)
COMMIT = "77543fe41def472179cfe4554e7ca27d64eff481"


def bead(bead_id: str, stage: str, parent: str, **metadata) -> Bead:
    return Bead(bead_id, bead_id, SprintState.OPEN, ("phase-bh", f"stage:{stage}"), parent, (), None, metadata, NOW, None, None)


# Planted after the real atm-bh-5 beads: the sprint container, its poured group and a poured finding group.
ROOT = Bead("atm-phase-bh", "phase-bh", SprintState.OPEN, ("phase-bh",), None, (), None, {}, NOW, None, None)
CONTAINER = bead("atm-bh-5", "sprint", "atm-phase-bh", layer=5, branch=SPRINT.branch, pr_target=SPRINT.target,
                 stack="phase-bh")
DEV = bead("atm-bh-5.group-dev", "dev", "atm-bh-5", sprint="bh-5", sprint_bead="atm-bh-5", layer=5)
SANITY = bead("atm-bh-5.group-sanity", "dev-sanity", "atm-bh-5", dev_bead="atm-bh-5.group-dev",
              branch=SPRINT.branch, worktree="/worktrees/sprint/bh-5-git-github-source")
SPRINT_QA = bead("atm-bh-5.group-qa", "qa", "atm-bh-5", checked_bead="atm-bh-5.group-dev", round=1, sprint="bh-5",
                 sprint_bead="atm-bh-5", branch=SPRINT.branch, worktree="/worktrees/sprint/bh-5-git-github-source")
FIX = bead("atm-bh-5.qa1-f1-r1-fix", "fix", "atm-bh-5", round=1, sprint_bead="atm-bh-5", finding_ref="qa1-f1")
FIX_QA = bead("atm-bh-5.qa1-f1-r1-qa", "qa", "atm-bh-5", checked_bead=FIX.id, round=1, sprint="bh-5",
              sprint_bead="atm-bh-5", branch="fix/bh-5-qa-r1", worktree="/worktrees/fix/bh-5-qa-r1")
PARENTS = {item.id: item for item in (ROOT, CONTAINER, DEV, SANITY, SPRINT_QA, FIX, FIX_QA)}


def mapped(value: Bead) -> Step:
    result = step(value, PARENTS)
    assert isinstance(result, Ok), result
    return result.value


def context(value: Step, pr: PullRequest | None) -> DispatchContext:
    return DispatchContext(value, SPRINT, "atm-phase-bh", "atm-bd-console", "fenix", COMMIT,
                           pr.base if pr else value.target or SPRINT.target, value.worktree or "", pr, LAYER_PRS, DISPATCH_REPO)


def expected(kind: str, pr: PullRequest | None) -> tuple[str, dict]:
    pr_fields = {"pr_number": str(pr.number) if pr else "", "pr_url": pr.url if pr else ""}
    base = pr.base if pr else SPRINT.target
    if kind == "sanity":
        return "dev-sanity-template.xml.j2", {
            "task_id": "atm-bh-5.group-sanity", "checked_bead": "atm-bh-5.group-dev", "sprint": "",
            "worktree_path": "/worktrees/sprint/bh-5-git-github-source", "branch": SPRINT.branch, "commit": COMMIT,
            "base": base, **pr_fields, "primary_checkout": "/repo", "lint_command": "just lint", "layer_prs": [140],
        }
    qa_common = {
        "phase": "atm-phase-bh", "phase_feature": "atm-bd-console", "stack": "phase-bh", "layer": 5,
        "policy_path": ".claude/project/quality-policy.md", "reviewers_round1": ["req-qa", "arch-qa"], "layer_prs": [140],
        "sprint_bead": "atm-bh-5", "sprint": "bh-5", "commit": COMMIT, "base": base, **pr_fields,
    }
    if kind == "sprint_qa":
        return "qa-template.xml.j2", {
            "task_id": "atm-bh-5.group-qa", "checked_bead": "atm-bh-5.group-dev", "round": 1,
            "worktree_path": "/worktrees/sprint/bh-5-git-github-source", "branch": SPRINT.branch, **qa_common,
        }
    return "qa-template.xml.j2", {
        "task_id": FIX_QA.id, "checked_bead": FIX.id, "round": 2, "carry_forward": FIX.id,
        "worktree_path": "/worktrees/fix/bh-5-qa-r1", "branch": "fix/bh-5-qa-r1", **qa_common,
    }


STEPS = {"sanity": SANITY, "sprint_qa": SPRINT_QA, "fix_qa": FIX_QA}


def render(template: str, values: dict, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    binary = shutil.which("sc-compose")
    assert binary, "sc-compose is required for the D5 render check: install sc-compose >= 1.7.0"
    var_file = tmp_path / "vars.json"
    var_file.write_text(json.dumps(values))
    return run_process(
        "strict template render",
        (binary, "render", "--strict", "--root", str(TEMPLATES), "--file", str(TEMPLATES / template),
         "--var-file", str(var_file)),
        check=False,
    )


@pytest.mark.parametrize("pr", [PR, None], ids=["with-pr", "no-pr"])
@pytest.mark.parametrize("kind", list(STEPS))
def test_dispatch_spec_vars_are_exact_and_render_strict(kind: str, pr: PullRequest | None, tmp_path: Path) -> None:
    spec = dispatch_spec(context(mapped(STEPS[kind]), pr), PHASE_TEMPLATES)
    assert isinstance(spec, Ok), spec
    template, values = expected(kind, pr)
    assert spec.value.template == f".claude/skills/atm-bd-orchestration/templates/{template}"
    assert dict(spec.value.vars) == values
    rendered = render(template, dict(spec.value.vars), tmp_path)
    assert rendered.returncode == 0, rendered.stderr
    verification = f"This is fix verification of fix bead `{FIX.id}`"
    assert (verification in rendered.stdout) is (kind == "fix_qa")


def test_dispatch_spec_uses_named_template_variable_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(DISPATCH_KEYS, "task", "task_identifier")

    spec = dispatch_spec(context(mapped(SANITY), None), PHASE_TEMPLATES)

    assert isinstance(spec, Ok), spec
    assert spec.value.vars["task_identifier"] == SANITY.id
    assert "task_id" not in spec.value.vars


def test_fix_verification_round_is_the_fix_beads_round_plus_one() -> None:
    later = {**PARENTS, FIX.id: bead(FIX.id, "fix", "atm-bh-5", round=2, sprint_bead="atm-bh-5")}
    assert step(FIX_QA, later).value.qa_round == 3
    assert mapped(SPRINT_QA).qa_round == 1


def test_fix_bead_without_round_is_not_found_naming_it() -> None:
    missing = {**PARENTS, FIX.id: bead(FIX.id, "fix", "atm-bh-5", sprint_bead="atm-bh-5")}
    result = step(FIX_QA, missing)
    assert isinstance(result, Err) and result.error == NotFound("not_found", f"{FIX.id}.metadata.round",
                                                               "add the required ATM BD value")


def test_qa_step_without_round_is_not_found_not_round_one() -> None:
    from dataclasses import replace

    result = dispatch_spec(context(replace(mapped(SPRINT_QA), qa_round=None), PR), PHASE_TEMPLATES)
    assert isinstance(result, Err) and isinstance(result.error, NotFound)
    assert result.error.what == "atm-bh-5.group-qa.metadata.round"


# D4 over recorded and planted beads, read through beads_source from a fake bd.
# show-findings.json: `bd show` of the real atm-bh-7 finding group qa1-f1-r1 (fix, sanity, QA) and the important
# finding atm-bh-7.group-qa-f10 with its sanity and qa-bead.json.j2 QA (no sprint_bead), atm-core 2026-10-15.
# planted.json: a foreign epic with a container and a quick-fix anchor dev step, a plan review, a dev whose
# parent is not shown, and two QA beads missing round or checked_bead.
BEADS = Path(__file__).parent / "fixtures" / "beads"


def recorded(fake_bin, monkeypatch: pytest.MonkeyPatch, *names: str) -> tuple[Bead, ...]:
    """Each file replayed as one `bd show` reply."""
    fake_bin.make("bd", f'cat "{BEADS}/$FAKE_FILE"\n')
    beads: list[Bead] = []
    for name in names:
        monkeypatch.setenv("FAKE_FILE", name)
        result = beads_source.show(("replayed",))
        assert isinstance(result, Ok), result
        beads.extend(result.value)
    return tuple(beads)


@pytest.fixture
def d4(fake_bin, monkeypatch: pytest.MonkeyPatch) -> tuple[PhaseFile, Phase, dict[str, Bead]]:
    open_beads = recorded(fake_bin, monkeypatch, "list-open.json")
    shown = recorded(fake_bin, monkeypatch, "show-closed.json", "show-findings.json", "planted.json")
    file = phase_file(str(BEADS / "repo"), "bh").value
    root = next(item for item in open_beads if item.id == "atm-phase-bh")
    result = phase(file, root, open_beads, (), shown)
    assert isinstance(result, Ok), result
    return file, result.value, {item.id: item for item in shown + open_beads}


def test_planted_steps_map_to_each_step_kind(d4) -> None:
    _, mapped_phase, _ = d4
    kinds = {item.bead_id: item.kind for item in mapped_phase.steps}
    assert {kinds[bead_id] for bead_id in (
        "atm-bh-7.group-dev", "atm-bh-7.qa1-f1-r1-fix", "atm-bh-7.qa1-f1-r1-sanity", "atm-bh-7.qa1-f1-r1-qa",
        "atm-bh-7.group-qa-f10", "atm-bh-7.plan-review", "zz-other-1", "zz-phase-other",
    )} == set(get_args(StepKind))
    assert (kinds["atm-bh-7.group-dev"], kinds["atm-bh-7.qa1-f1-r1-fix"], kinds["atm-bh-7.qa1-f1-r1-sanity"],
            kinds["atm-bh-7.qa1-f1-r1-qa"], kinds["atm-bh-7.group-qa-f10"], kinds["atm-bh-7.plan-review"],
            kinds["zz-other-1"], kinds["zz-phase-other"]) == (
        "dev", "fix", "sanity", "qa", "finding", "plan_review", "container", "other")


def test_qa_and_unknown_stage_classification_use_named_kinds() -> None:
    assert _kind(bead("atm-bh-7.qa1-f1-r1-qa", "qa", "atm-bh-7")) == QA_KIND
    assert _kind(bead("atm-bh-7.unrecognized", "unrecognized", "atm-bh-7")) == OTHER_KIND


def test_foreign_top_epic_is_kept_and_a_broken_chain_is_none(d4) -> None:
    _, mapped_phase, _ = d4
    steps = {item.bead_id: item for item in mapped_phase.steps}
    foreign = steps["zz-other-1.group-dev"]
    assert (foreign.top_epic, foreign.tag) == ("zz-phase-other", "phase-other")
    assert steps["atm-bh-7.qa1-f1-r1-fix"].top_epic == "atm-phase-bh"
    assert steps["atm-bh-7.orphan-dev"].top_epic is None


def test_quick_fix_and_anchor_are_set(d4) -> None:
    _, mapped_phase, _ = d4
    steps = {item.bead_id: item for item in mapped_phase.steps}
    assert (steps["zz-other-1.group-dev"].quick_fix, steps["zz-other-1.group-dev"].anchor) == (True, True)
    assert (steps["atm-bh-7.group-dev"].quick_fix, steps["atm-bh-7.group-dev"].anchor) == (False, False)


def test_finding_group_qa_carries_its_fix_bead(d4) -> None:
    _, mapped_phase, _ = d4
    qa = next(item for item in mapped_phase.steps if item.bead_id == "atm-bh-7.qa1-f1-r1-qa")
    assert (qa.checked_bead, qa.carry_forward, qa.sprint_bead, qa.qa_round) == (
        "atm-bh-7.qa1-f1-r1-fix", "atm-bh-7.qa1-f1-r1-fix", "atm-bh-7", 2)
    sanity = next(item for item in mapped_phase.steps if item.bead_id == "atm-bh-7.qa1-f1-r1-sanity")
    assert sanity.checked_bead == "atm-bh-7.qa1-f1-r1-fix"


def test_qa_bead_template_qa_gets_sprint_bead_from_its_finding(d4) -> None:
    _, mapped_phase, beads = d4
    assert "sprint_bead" not in beads["atm-bh-7.group-qa-f10-qa"].metadata
    qa = next(item for item in mapped_phase.steps if item.bead_id == "atm-bh-7.group-qa-f10-qa")
    assert (qa.checked_bead, qa.sprint_bead, qa.carry_forward) == (
        "atm-bh-7.group-qa-f10", "atm-bh-7", "atm-bh-7.group-qa-f10")


def test_qa_without_round_or_checked_bead_is_a_step_error_and_every_other_step_is_present(d4) -> None:
    file, mapped_phase, beads = d4
    errors = dict(mapped_phase.step_errors)
    assert errors["atm-bh-7.qa-no-round"] == NotFound("not_found", "atm-bh-7.qa-no-round.metadata.round",
                                                      "add the required ATM BD value")
    assert errors["atm-bh-7.qa-no-checked"] == NotFound("not_found", "atm-bh-7.qa-no-checked.metadata.checked_bead",
                                                        "add the required ATM BD value")
    step_ids = {item.bead_id for item in mapped_phase.steps}
    assert not step_ids & {"atm-bh-7.qa-no-round", "atm-bh-7.qa-no-checked"}
    others = set(beads) - set(errors) - set(file.sprints) - {"atm-phase-bh"}
    assert step_ids == others


@pytest.mark.parametrize("kind", ["dev", "fix", "finding", "plan_review", "container", "other"])
def test_unsupported_kind_is_config_invalid_not_a_missing_value(kind: str) -> None:
    from dataclasses import replace

    result = dispatch_spec(context(replace(mapped(SANITY), kind=kind), PR), PHASE_TEMPLATES)
    assert isinstance(result, Err) and isinstance(result.error, ConfigInvalid)
    assert (result.error.key, result.error.value) == ("atm-bh-5.group-sanity.kind", kind)


def test_group_suffix_names_only_poured_group_beads() -> None:
    assert [group_suffix(bead_id) for bead_id in (
        "atm-bh-5.group-dev", "atm-bh-5.group-sanity", "atm-bh-5.group-qa", "atm-bh-5.group-qa-f1", "atm-bh-5",
    )] == ["dev", "sanity", "qa", None, None]
