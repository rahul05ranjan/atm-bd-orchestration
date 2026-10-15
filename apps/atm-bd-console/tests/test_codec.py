from __future__ import annotations

from datetime import UTC, datetime, timedelta

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
    Timeout,
    Unreachable,
)
from atm_bd_console.types import Config, GhStatus, Inputs, RepoConfig, Sprint


def test_config_round_trip_and_literal_rejection() -> None:
    value = Config("bh", "fenix", "rest", 60, False, False, "/repo", "/state")
    decoded = from_json(Config, to_json(value))
    assert decoded == Ok(value)
    invalid = from_json(Config, to_json(value).replace('"rest"', '"wrong"'))
    assert isinstance(invalid, Err)
    assert isinstance(invalid.error, ParseFailed)
    assert "github" in invalid.error.message


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
    snapshot = type("SnapshotLike", (), {"generated_at": now - timedelta(seconds=120), "interval_s": 60})()
    assert not snapshot_stale(snapshot, now)
    snapshot = type("SnapshotLike", (), {"generated_at": now - timedelta(seconds=121), "interval_s": 60})()
    assert snapshot_stale(snapshot, now)


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
