from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest

from atm_bd_console.codec import from_json, to_json
from atm_bd_console.result import CommandFailed, Err, NotFound, Ok, ParseFailed
from atm_bd_console.types import Config, GhStatus, Inputs, RepoConfig, Sprint

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


def _parse_failed(result: Any) -> ParseFailed:
    assert isinstance(result, Err), result
    assert isinstance(result.error, ParseFailed), result.error
    return result.error


def _config() -> Config:
    return Config("bh", "fenix", "atm-dev", "rest", 60, False, False, "/repo", "/state")


def test_unknown_field_is_rejected_with_its_path() -> None:
    text = to_json(_config())[:-1] + ',"extra":1}'
    error = _parse_failed(from_json(Config, text))
    assert error.source == "$.extra"
    assert "unknown field" in error.message


def test_nested_unknown_field_is_rejected() -> None:
    text = to_json(GhStatus("off", None, None, None, False))[:-1] + ',"surplus":true}'
    assert _parse_failed(from_json(GhStatus, text)).source == "$.surplus"


def test_missing_field_path_is_in_source() -> None:
    error = _parse_failed(from_json(Config, '{"phase":"bh"}'))
    assert error.source == "$.lead"
    assert error.message == "$.lead: missing field"


def test_unsupported_annotation_is_an_err_not_a_fall_through() -> None:
    @dataclass(frozen=True)
    class Odd:
        value: set[str]

    error = _parse_failed(from_json(Odd, '{"value":["a"]}'))
    assert error.source == "$.value"
    assert "unsupported annotation" in error.message


def test_deep_nesting_is_an_err_not_a_recursion_error() -> None:
    error = _parse_failed(from_json(Any, "[" * 100_000 + "]" * 100_000))
    assert error.source == "$"
    assert "nested too deeply" in error.message


def test_union_failure_keeps_the_inner_path() -> None:
    repo = RepoConfig("atm", "dev-sanity", "quality-mgr", "/repo", "/worktrees", "just lint", "/policy", (), "/plans")
    sprint = Sprint("bh-6", "tick rules", 6, "sprint/bh-6", "integrate/phase-bh", "phase-bh", (), False, 0)
    inputs = Inputs(
        NOW, "fenix", repo, 60, Err(NotFound("not_found", "phase", "load it")), Ok(()), Ok(()), Ok(()),
        None, None, None, GhStatus("off", None, None, None, False), Ok(()),
    )
    assert from_json(Sprint, to_json(sprint)) == Ok(sprint)
    # members is Result[tuple[Member, ...]]: the Ok alternative fails inside its value.
    text = to_json(inputs).replace('"members":{"ok":[]}', '"members":{"ok":[{"name":"x","presence":"lost"}]}')
    error = _parse_failed(from_json(Inputs, text))
    assert error.source == "$.members.ok[0].presence"


def test_optional_error_union_reports_kind_path() -> None:
    text = to_json(GhStatus("graphql", False, CommandFailed("command_failed", ("gh",), 1, "x", "retry"), NOW, False))
    error = _parse_failed(from_json(GhStatus, text.replace('"command_failed"', '"bogus"')))
    assert error.source == "$.error.kind"
    error = _parse_failed(from_json(GhStatus, text.replace('"code":1', '"code":"1"')))
    assert error.source == "$.error.code"


@pytest.mark.parametrize("text", ["{", "", "[1,"])
def test_invalid_json_is_an_err(text: str) -> None:
    assert _parse_failed(from_json(Config, text)).source == "$"
