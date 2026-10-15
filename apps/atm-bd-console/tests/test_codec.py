from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass, replace
from datetime import UTC, datetime, timedelta, timezone
from typing import get_type_hints

import pytest
from contract_samples import ERRORS, NOT_FOUND, PLUS_TWO, SAMPLES, SNAPSHOT, TASK

from atm_bd_console import result, types
from atm_bd_console.codec import from_json, snapshot_stale, to_json
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
from atm_bd_console.types import Config, GhStatus, Inputs, RepoConfig, Snapshot, Sprint


def test_config_round_trip_and_literal_rejection() -> None:
    value = Config("bh", "fenix", "rest", 60, False, False, "/repo", "/state")
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
            assert value is not None, f"{type(sample).__name__}.{field.name} is None"
            assert not (isinstance(value, (tuple, Mapping, str)) and len(value) == 0), (
                f"{type(sample).__name__}.{field.name} is empty"
            )


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
    repo = RepoConfig("atm", "dev-sanity", "quality-mgr", "/repo", "/worktrees", "just lint", "/policy", (), "/plans")
    sprint = Sprint("bh-6", "tick rules", 6, "sprint/bh-6-tick-rules", "sprint/bh-1-contract", "phase-bh", (), False, 0)
    now = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
    inputs = Inputs(now, "fenix", repo, 60, Err(NotFound("not_found", "phase", "load it")), Ok(()), Ok(()), Ok(()), None, None, None, GhStatus("off", None, None, None, False))
    assert from_json(Sprint, to_json(sprint)) == Ok(sprint)
    assert from_json(Inputs, to_json(inputs)) == Ok(inputs)


def test_missing_field_and_datetime_errors_are_results() -> None:
    missing = from_json(Config, '{"phase":"bh"}')
    assert isinstance(missing, Err)
    assert "lead" in missing.error.message
    invalid = from_json(datetime, '"2026-10-14T10:00:00"')
    assert isinstance(invalid, Err)


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
        IoFailed("io_failed", "/tmp/state", "denied", "repair permissions"),
    )
    for error in errors:
        assert from_json(Err, to_json(Err(error))) == Ok(Err(error))
