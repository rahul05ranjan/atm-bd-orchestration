"""D2: phase_file and repo_config on copies of atm-core's real phase-bh files.

tests/fixtures/beads/repo/ holds verbatim copies of atm-core's `.atm-bd/phase-bh.toml`,
`docs/plans/phase-bh.jsonl` and `.claude/project/atm-bd-orchestration.yaml` (2026-10-14).
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from atm_bd_console.atm_bd import phase_file, repo_config
from atm_bd_console.result import ConfigInvalid, Err, IoFailed, NotFound, Ok, ParseFailed
from atm_bd_console.types import PhaseFile, RepoConfig

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
                                  {"zz-bh-1": (), "zz-bh-2": ("zz-bh-1",)}))


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
    toml.write_text("".join(line + "\n" for line in toml.read_text().splitlines() if not line.startswith(key)))
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


def _with_primary_checkout(repo: Path) -> Path:
    config = repo / CONFIG
    config.write_text(config.read_text() + 'primary_checkout: "/Users/randlee/github/atm-core"\n')
    return config


def test_repo_config_on_the_real_file_names_the_key_it_lacks() -> None:
    # atm-core's installed file has no primary_checkout; the design reads it from this file, so it is NotFound.
    error = _err(repo_config(str(REPO)))
    assert isinstance(error, NotFound)
    assert error.what == f"{REPO / CONFIG}: key primary_checkout"


def test_repo_config_yields_every_field(repo: Path) -> None:
    _with_primary_checkout(repo)
    assert repo_config(str(repo)) == Ok(RepoConfig(
        "atm", "atm-sanity", "quality-mgr", "/Users/randlee/github/atm-core", "../atm-core-worktrees", "just lint",
        ".claude/project/quality-policy.md", REVIEWERS, "docs/plans",
    ))


def test_repo_config_without_qa_member_is_not_found_naming_it(repo: Path) -> None:
    config = _with_primary_checkout(repo)
    config.write_text("".join(line + "\n" for line in config.read_text().splitlines()
                              if not line.startswith("qa_member:")))
    error = _err(repo_config(str(repo)))
    assert isinstance(error, NotFound)
    assert error.what == f"{config}: key qa_member"


def test_repo_config_reads_a_block_reviewer_list(repo: Path) -> None:
    config = _with_primary_checkout(repo)
    lines = [line for line in config.read_text().splitlines() if not line.startswith("reviewers_round1:")]
    config.write_text("\n".join([*lines, "reviewers_round1:", "  - req-qa", '  - "arch-qa"']) + "\n")
    result = repo_config(str(repo))
    assert isinstance(result, Ok)
    assert result.value.reviewers_round1 == ("req-qa", "arch-qa")


def test_repo_config_reviewers_not_a_list_is_config_invalid(repo: Path) -> None:
    config = _with_primary_checkout(repo)
    lines = [line for line in config.read_text().splitlines() if not line.startswith("reviewers_round1:")]
    config.write_text("\n".join([*lines, 'reviewers_round1: "req-qa"']) + "\n")
    error = _err(repo_config(str(repo)))
    assert isinstance(error, ConfigInvalid)
    assert error.key == "reviewers_round1"


def test_repo_config_missing_file_is_not_found(tmp_path: Path) -> None:
    error = _err(repo_config(str(tmp_path)))
    assert isinstance(error, NotFound)
    assert error.what == str(tmp_path / CONFIG)
