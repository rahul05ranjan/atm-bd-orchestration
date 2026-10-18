from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import get_args, get_type_hints

import pytest
from contract_samples import (
    BEAD,
    CONFIG,
    ERRORS,
    INPUTS,
    NOT_FOUND,
    PHASE,
    PLUS_TWO,
    PRS_FILE,
    SAMPLES,
    SNAPSHOT,
    STEP,
    TASK,
)

from atm_bd_console import result, types
from atm_bd_console.codec import _ERRORS, from_json, snapshot_stale, to_json
from atm_bd_console.enums import CiState, MergeState, PrState, QaState, SprintState
from atm_bd_console.icons import icon_for
from atm_bd_console.result import (
    CommandFailed,
    ConfigInvalid,
    Err,
    IoFailed,
    LockHeld,
    NotFound,
    Ok,
    ParseFailed,
    Rejected,
    Result,
    Timeout,
    Unreachable,
)
from atm_bd_console.types import Config, Inputs, RepoConfig, Snapshot, Sprint

TEST_LEAD = "test-lead"
TEST_SANITY_MEMBER = "test-sanity"
TEST_QA_MEMBER = "test-qa"
TEST_REVIEWER = "test-reviewer"
OPTIONAL_ADDITIONS = {"agent_type", "model", "harness", "qa_state", "close_reason", "severity", "assignee",
                      "core_remaining", "core_limit", "core_reset", "graphql_remaining", "graphql_limit",
                      "graphql_reset", "quota_at"}


def test_pre_enum_snapshot_decodes_to_typed_states() -> None:
    fixture = Path(__file__).parent / "fixtures" / "snapshot" / "pre-enums.json"
    decoded = from_json(Snapshot, fixture.read_text())
    assert isinstance(decoded, Ok)
    pr = decoded.value.prs[0]
    assert isinstance(pr.pr.ci, CiState)
    assert isinstance(pr.pr.state, PrState)
    assert pr.merge is not None and isinstance(pr.merge.state, MergeState)


def test_unrelated_dataclasses_with_domain_names_are_not_migrated() -> None:
    @dataclass
    class Snapshot:
        top_ci: str

    @dataclass
    class PullRequest:
        ci: str
        failed_checks: tuple[str, ...]

    assert from_json(Snapshot, '{"top_ci":"success"}') == Ok(Snapshot("success"))
    assert from_json(PullRequest, '{"ci":"failure","failed_checks":["linux"]}') == Ok(
        PullRequest("failure", ("linux",))
    )


@pytest.mark.parametrize(
    ("legacy", "checks", "expected"),
    [
        ("success", [], CiState.PASS),
        ("pending", [], CiState.RUNNING),
        ("failure", ["test-macos"], CiState.FAIL_MACOS),
        ("failure", ["test-windows"], CiState.FAIL_WINDOWS),
        ("failure", ["test-linux"], CiState.FAIL_LINUX),
        ("failure", ["test-linux", "test-macos"], CiState.FAIL_BUG),
        ("failure", [], CiState.FAIL_BUG),
    ],
)
def test_legacy_prs_file_and_snapshot_ci_migration(legacy: str, checks: list[str], expected: CiState) -> None:
    prs_file = json.loads(to_json(PRS_FILE))
    prs_file["prs"][0].update(ci=legacy, failed_checks=checks)
    decoded_file = from_json(types.PrsFile, json.dumps(prs_file))
    assert isinstance(decoded_file, Ok)
    assert decoded_file.value.prs[0].ci is expected

    snapshot = json.loads(to_json(SNAPSHOT))
    snapshot["top_ci"] = legacy
    snapshot["prs"][0]["pr"].update(ci=legacy, failed_checks=checks)
    decoded_snapshot = from_json(types.Snapshot, json.dumps(snapshot))
    assert isinstance(decoded_snapshot, Ok)
    assert decoded_snapshot.value.top_ci is {
        "success": CiState.PASS, "pending": CiState.RUNNING, "failure": CiState.FAIL_BUG,
    }[legacy]
    assert decoded_snapshot.value.prs[0].pr.ci is expected


@pytest.mark.parametrize("checks", [None, "linux", [123], {"test": "linux"}])
def test_legacy_ci_rejects_malformed_failed_checks(checks: object) -> None:
    prs_file = json.loads(to_json(PRS_FILE))
    prs_file["prs"][0].update(ci="failure", failed_checks=checks)
    decoded = from_json(types.PrsFile, json.dumps(prs_file))
    assert isinstance(decoded, Err)
    assert isinstance(decoded.error, ParseFailed)
    assert decoded.error.source.startswith("$.prs[0].failed_checks")


def test_legacy_bead_step_and_qa_strings_decode_to_enums() -> None:
    import json

    bead = json.loads(to_json(BEAD))
    bead.pop("raw_status")
    decoded_bead = from_json(types.Bead, json.dumps(bead))
    decoded_step = from_json(types.Step, to_json(STEP))
    agent = replace(SNAPSHOT.agents[0], qa_state=QaState.PASS)
    decoded_agent = from_json(types.AgentView, to_json(agent))
    assert isinstance(decoded_bead, Ok) and decoded_bead.value.status is SprintState.IN_PROGRESS
    assert isinstance(decoded_step, Ok) and decoded_step.value.status is SprintState.IN_PROGRESS
    assert isinstance(decoded_agent, Ok) and decoded_agent.value.qa_state is QaState.PASS
    assert '"qa_state":"pass"' in to_json(agent)


def test_config_round_trip_and_literal_rejection() -> None:
    value = Config("bh", TEST_LEAD, "atm-dev", "rest", 60, False, False, "/repo", "/state")
    decoded = from_json(Config, to_json(value))
    assert decoded == Ok(value)
    invalid = from_json(Config, to_json(value).replace('"rest"', '"wrong"'))
    assert isinstance(invalid, Err)
    assert isinstance(invalid.error, ParseFailed)
    assert invalid.error.source == "$.github"
    assert invalid.error.message.startswith("$.github: expected one of")


def test_nested_literal_rejection_names_the_full_path() -> None:
    text = to_json(SNAPSHOT).replace('"mode":"graphql"', '"mode":"soap"', 1)
    invalid = from_json(Snapshot, text)
    assert isinstance(invalid, Err)
    assert isinstance(invalid.error, ParseFailed)
    assert invalid.error.source == "$.gh.mode"
    invalid = from_json(Snapshot, to_json(SNAPSHOT).replace('"kind":"sprint_qa"', '"kind":"qa"'))
    assert isinstance(invalid, Err)
    assert invalid.error.source == "$.agents[0].rows[0].kind"


def _contract_classes() -> set[type]:
    return {
        value for module in (result, types) for value in vars(module).values()
        if isinstance(value, type) and is_dataclass(value) and value.__module__ == module.__name__
    }


def test_samples_cover_every_contract_type_populated() -> None:
    assert {type(sample) for sample in SAMPLES} | {Ok, Err} == _contract_classes()
    for sample in SAMPLES:
        for field in fields(sample):
            value = getattr(sample, field.name)
            assert value is not None or field.name in OPTIONAL_ADDITIONS, (
                f"{type(sample).__name__}.{field.name} is None"
            )
            if field.name in OPTIONAL_ADDITIONS and value is None:
                continue
            assert not (isinstance(value, (tuple, Mapping, str)) and len(value) == 0), (
                f"{type(sample).__name__}.{field.name} is empty"
            )


def test_old_pr_cache_decodes_with_quota_defaults() -> None:
    value = json.loads(to_json(PRS_FILE))
    value.pop("attempted_at")
    for name in ("quota_remaining", "quota_limit", "quota_reset", "paused", "rate_limited",
                 "poll_interval_s", "quota_threshold", "core_remaining", "core_limit", "core_reset",
                 "graphql_remaining", "graphql_limit", "graphql_reset", "quota_at"):
        value["gh"].pop(name)
    decoded = from_json(types.PrsFile, json.dumps(value))
    assert isinstance(decoded, Ok)
    assert decoded.value.attempted_at is None
    assert decoded.value.gh.quota_remaining is None and decoded.value.gh.quota_reset is None
    assert not decoded.value.gh.paused and not decoded.value.gh.rate_limited
    assert (decoded.value.gh.poll_interval_s, decoded.value.gh.quota_threshold) == (600, 50)


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda sample: type(sample).__name__)
def test_every_contract_type_round_trips(sample: object) -> None:
    text = to_json(sample)
    assert from_json(type(sample), text) == Ok(sample)
    assert to_json(from_json(type(sample), text).value) == text


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda sample: type(sample).__name__)
def test_ok_and_err_round_trip(sample: object) -> None:
    assert from_json(Ok[type(sample)], to_json(Ok(sample))) == Ok(Ok(sample))
    error = sample if type(sample) in {type(item) for item in ERRORS} else NOT_FOUND
    assert from_json(Err, to_json(Err(error))) == Ok(Err(error))
    assert from_json(Result[type(sample)], to_json(Err(error))) == Ok(Err(error))


def test_non_utc_aware_datetime_is_written_as_utc_z() -> None:
    assert to_json(PLUS_TWO) == '"2026-10-14T11:30:00Z"'
    assert to_json(datetime(2026, 10, 14, 7, 0, tzinfo=timezone(timedelta(hours=-5)))) == '"2026-10-14T12:00:00Z"'
    decoded = from_json(datetime, to_json(PLUS_TWO))
    assert decoded == Ok(PLUS_TWO)
    assert decoded.value.tzinfo is UTC
    assert '"assigned_at":"2026-10-14T11:53:00Z","description":"implement tick rules","id":"atm-bh-6"' in to_json(TASK)
    assert '"state_since":"2026-10-14T11:30:00Z"' in to_json(TASK)


def test_tick_context_round_trip() -> None:
    sprint = PHASE.sprints[0]
    repo = RepoConfig(
        "atm", TEST_SANITY_MEMBER, TEST_QA_MEMBER, "/repo", "/worktrees", "just lint", "/policy",
        (TEST_REVIEWER,), "/plans",
    )
    inputs = replace(INPUTS, lead=TEST_LEAD, repo=repo, phase=Ok(PHASE))
    assert sprint.stack == "phase-bh"
    assert repo.reviewers_round1 == (TEST_REVIEWER,)
    assert from_json(Sprint, to_json(sprint)) == Ok(sprint)
    assert from_json(Inputs, to_json(inputs)) == Ok(inputs)


def test_legacy_snapshot_decodes_with_empty_agent_metadata() -> None:
    import json

    legacy = json.loads((Path(__file__).parent / "fixtures" / "contract" / "snapshot-running.json").read_text())
    for agent in legacy["agents"]:
        for field in ("agent_type", "model", "harness", "qa_state"):
            agent.pop(field, None)
    for pr in legacy["prs"]:
        pr.pop("assignee", None)
    decoded = from_json(Snapshot, json.dumps(legacy))
    assert isinstance(decoded, Ok)
    assert all((agent.agent_type, agent.model, agent.harness, agent.qa_state) == (None, None, None, None)
               for agent in decoded.value.agents)


@pytest.mark.parametrize(
    ("qa_state", "color"),
    ((QaState.RUNNING, "#4A5AF8"), (QaState.PASS, "#2EA043"), (QaState.FAIL, "#F85149")),
)
def test_qa_icon_color_survives_snapshot_codec_round_trip(qa_state: QaState, color: str) -> None:
    agent = replace(SNAPSHOT.agents[0], agent_type="qa", qa_state=qa_state)
    snapshot = replace(SNAPSHOT, agents=(agent,))

    encoded = to_json(snapshot)
    decoded = from_json(Snapshot, encoded)

    assert decoded == Ok(snapshot)
    assert to_json(decoded.value) == encoded
    assert icon_for(decoded.value.agents[0]) == ("\U000f029b", color)


def test_inputs_interval_matches_config_interval() -> None:
    assert INPUTS.interval_s == CONFIG.interval_s


def test_tick_context_missing_contract_fields_report_paths() -> None:
    import json

    cases = (
        (Sprint, to_json(PHASE.sprints[0]), "stack", "$.stack"),
        (Inputs, to_json(INPUTS), "repo", "$.repo"),
        (Inputs, to_json(INPUTS), "interval_s", "$.interval_s"),
    )
    for cls, text, field, path in cases:
        value = json.loads(text)
        value.pop(field)
        decoded = from_json(cls, json.dumps(value))
        assert isinstance(decoded, Err)
        assert isinstance(decoded.error, ParseFailed)
        assert decoded.error.source == path


def test_missing_field_and_datetime_errors_are_results() -> None:
    missing = from_json(Config, '{"phase":"bh"}')
    assert isinstance(missing, Err)
    assert "lead" in missing.error.message
    invalid = from_json(datetime, '"2026-10-14T10:00:00"')
    assert isinstance(invalid, Err)


@pytest.mark.parametrize(
    "text",
    [
        '"2026-1-4T1:02:03Z"',
        '"2026-10-14T11:30:00.123456Z"',
    ],
)
def test_datetime_decoder_rejects_noncanonical_format(text: str) -> None:
    invalid = from_json(datetime, text)
    assert isinstance(invalid, Err)
    assert isinstance(invalid.error, ParseFailed)
    assert invalid.error.source == "$"


def test_datetime_encoder_documents_precision_and_rejects_naive_values() -> None:
    with pytest.raises(ValueError, match="naive datetime"):
        to_json(datetime(2026, 10, 14, 11, 30, 0))
    value = datetime(2026, 10, 14, 11, 30, 0, 1, tzinfo=UTC)
    assert to_json(value) == '"2026-10-14T11:30:00Z"'


def test_datetime_encodes_utc_and_snapshot_staleness_is_strict() -> None:
    now = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
    assert to_json(now) == '"2026-10-14T12:00:00Z"'
    assert SNAPSHOT.interval_s == 60
    at_limit = replace(SNAPSHOT, generated_at=now - timedelta(seconds=120))
    assert not snapshot_stale(at_limit, now)
    past_limit = replace(SNAPSHOT, generated_at=now - timedelta(seconds=121))
    assert snapshot_stale(past_limit, now)
    slower = replace(past_limit, interval_s=61)
    assert not snapshot_stale(slower, now)


def test_snapshot_stale_signature_is_snapshot() -> None:
    assert get_type_hints(snapshot_stale)["snapshot"] is Snapshot


def test_each_error_kind_round_trips_inside_result() -> None:
    errors = (
        CommandFailed("command_failed", ("bd",), 1, "bad", "retry"),
        Timeout("timeout", ("bd",), 5, "retry"),
        Rejected("rejected", ("gh",), 403, "denied", "authenticate"),
        Unreachable("unreachable", ("gh",), "offline", "retry"),
        ParseFailed("parse_failed", "$.field", "bad", "correct input"),
        NotFound("not_found", "bead", "create it"),
        ConfigInvalid("config_invalid", "mode", "bad", "correct config"),
        LockHeld("lock_held", None, "wait"),
        IoFailed("io_failed", "state.json", "denied", "repair permissions"),
    )
    for error in errors:
        assert from_json(Err, to_json(Err(error))) == Ok(Err(error))


def test_error_kind_decoder_map_matches_error_union() -> None:
    expected = {
        get_args(get_type_hints(error_type)["kind"])[0]: error_type
        for error_type in get_args(result.Error)
    }
    assert _ERRORS == expected
