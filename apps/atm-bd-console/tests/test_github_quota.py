from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from typing import Literal

import pytest
from test_github_source import RECORDED_REST, TEST_REPO, graphql

from atm_bd_console import github_source
from atm_bd_console.codec import from_json, to_json
from atm_bd_console.command import Output
from atm_bd_console.result import CommandFailed, Err, Ok
from atm_bd_console.types import GhStatus

NOW = datetime(2026, 10, 18, 12, tzinfo=UTC)
RESET = NOW + timedelta(hours=1)
GRAPHQL_ARGV = github_source.graphql_argv(TEST_REPO)
REST_ARGV = github_source.rest_argv(TEST_REPO)
QUOTA_ARGV = github_source.quota_argv(TEST_REPO)


def open_prs(mode, code_root="/repo", branch_groups=(), budget=None):
    return github_source.open_prs(mode, code_root, branch_groups, budget, TEST_REPO)


def quota(remaining, resource="graphql"):
    return {"resources": {resource: {"remaining": remaining, "limit": 5000, "reset": int(RESET.timestamp())}}}


def budget(mode: Literal["graphql", "rest"] = "graphql", previous=None, now=NOW):
    return github_source.Budget(mode, "/code", 50, 600, now, previous or GhStatus(mode, None, None, None, False), TEST_REPO)


@pytest.mark.parametrize("mode,active", (("graphql", "graphql"), ("rest", "core")))
@pytest.mark.parametrize("active_low", (True, False))
def test_only_selected_resource_controls_pause(monkeypatch, mode, active, active_low):
    other = "core" if active == "graphql" else "graphql"
    resources = {active: quota(2450 if active_low else 4000, active)["resources"][active],
                 other: quota(4000 if active_low else 2450, other)["resources"][other]}
    calls = []

    def run(argv, *_args, **_kwargs):
        calls.append(argv)
        body = {"resources": resources} if argv == QUOTA_ARGV else graphql() if mode == "graphql" else []
        return Ok(Output(0, json.dumps(body)))

    monkeypatch.setattr(github_source.command, "run", run)
    result, status = open_prs(mode, "/repo", (), budget(mode))
    assert status.paused is active_low
    assert status.quota_remaining == (2450 if active_low else 4000)
    assert (status.core_remaining, status.graphql_remaining) == (
        resources["core"]["remaining"], resources["graphql"]["remaining"])
    if active_low:
        assert isinstance(result, Err) and calls == [QUOTA_ARGV]
    else:
        assert isinstance(result, Ok)
        assert calls == [QUOTA_ARGV, GRAPHQL_ARGV if mode == "graphql" else REST_ARGV]
@pytest.mark.parametrize("mode", ("graphql", "rest"))
def test_both_resources_come_from_existing_quota_request_and_survive_cache(monkeypatch, mode):
    calls = []
    resources = {"core": {"remaining": 4999, "limit": 5000, "reset": int(RESET.timestamp())},
                 "graphql": {"remaining": 4997, "limit": 5000, "reset": int((RESET + timedelta(minutes=5)).timestamp())}}

    def run(argv, *_args, **_kwargs):
        calls.append(argv)
        body = {"resources": resources} if argv == QUOTA_ARGV else graphql() if mode == "graphql" else []
        return Ok(Output(0, json.dumps(body)))

    monkeypatch.setattr(github_source.command, "run", run)
    result, status = open_prs(mode, "/repo", (), budget(mode))
    assert isinstance(result, Ok)
    assert calls == [QUOTA_ARGV, GRAPHQL_ARGV if mode == "graphql" else REST_ARGV]
    assert (status.core_remaining, status.core_limit, status.core_reset) == (4999, 5000, RESET)
    assert (status.graphql_remaining, status.graphql_limit, status.graphql_reset) == (4997, 5000, RESET + timedelta(minutes=5))
    assert status.quota_at == NOW
    decoded = from_json(GhStatus, to_json(status))
    assert isinstance(decoded, Ok)
    assert to_json(decoded.value) == to_json(status)


def test_missing_other_resource_does_not_disable_selected_guard(monkeypatch):
    monkeypatch.setattr(github_source.command, "run", lambda argv, *_args, **_kwargs: Ok(Output(0, json.dumps(
        quota(4000) if argv == QUOTA_ARGV else graphql()))))
    result, status = open_prs("graphql", "/repo", (), budget())
    assert isinstance(result, Ok) and status.graphql_remaining == 4000 and status.core_remaining is None


def test_latest_paginated_quota_is_retained_while_paused(monkeypatch):
    page = graphql(total=101)
    page["data"]["repository"]["pullRequests"]["pageInfo"] = {"hasNextPage": True, "endCursor": "next"}
    calls = []
    readings = iter(((4800, 4000), (4700, 2450)))

    def run(argv, *_args, **_kwargs):
        calls.append(argv)
        if argv == QUOTA_ARGV:
            core, graphql_remaining = next(readings)
            body = {"resources": {"core": quota(core, "core")["resources"]["core"],
                                  "graphql": quota(graphql_remaining)["resources"]["graphql"]}}
        else:
            body = page
        return Ok(Output(0, json.dumps(body)))

    monkeypatch.setattr(github_source.command, "run", run)
    result, status = open_prs("graphql", "/repo", (), budget())
    assert isinstance(result, Err) and status.paused
    assert (status.core_remaining, status.graphql_remaining) == (4700, 2450)
    assert calls == [QUOTA_ARGV, GRAPHQL_ARGV, QUOTA_ARGV]
    result, retained = open_prs("graphql", "/repo", (), budget(previous=status, now=NOW + timedelta(minutes=20)))
    assert isinstance(result, Err) and retained.quota_at == NOW
    assert (retained.core_remaining, retained.graphql_remaining) == (4700, 2450)
    assert calls == [QUOTA_ARGV, GRAPHQL_ARGV, QUOTA_ARGV]


def test_graphql_paginates_and_checks_shared_quota_before_every_page(monkeypatch):
    first = graphql(total=101)
    pulls = first["data"]["repository"]["pullRequests"]
    node = pulls["nodes"][0]
    pulls["nodes"] = [{**copy.deepcopy(node), "number": number} for number in range(100)]
    pulls["pageInfo"] = {"hasNextPage": True, "endCursor": "page-one"}
    second = graphql(total=101)
    second_pulls = second["data"]["repository"]["pullRequests"]
    second_pulls["nodes"][0]["number"] = 100
    second_pulls["pageInfo"] = {"hasNextPage": False, "endCursor": "page-two"}
    calls = []

    def run(argv, *_args, **_kwargs):
        calls.append(argv)
        body = quota(2500) if argv == QUOTA_ARGV else second if "cursor=page-one" in argv else first
        return Ok(Output(0, json.dumps(body)))

    monkeypatch.setattr(github_source.command, "run", run)
    result, status = open_prs("graphql", "/repo", (), budget())
    assert isinstance(result, Ok) and len(result.value) == 101
    assert not status.truncated and not status.paused
    assert calls[0] == calls[2] == QUOTA_ARGV
    assert calls[1] == GRAPHQL_ARGV and "cursor=page-one" in calls[3]


def test_pauses_at_49_percent_and_recovers_at_threshold(monkeypatch):
    remaining = [2450]
    calls = []

    def run(argv, *_args, **_kwargs):
        calls.append(argv)
        body = quota(remaining[0]) if argv == QUOTA_ARGV else graphql()
        return Ok(Output(0, json.dumps(body)))

    monkeypatch.setattr(github_source.command, "run", run)
    result, status = open_prs("graphql", "/repo", (), budget())
    assert isinstance(result, Err) and status.paused and status.quota_remaining == 2450
    assert status.quota_limit == 5000 and status.quota_reset == RESET
    assert calls == [QUOTA_ARGV]
    remaining[0] = 2500
    result, status = open_prs("graphql", "/repo", (), budget(previous=status))
    assert isinstance(result, Err) and status.paused and calls == [QUOTA_ARGV]
    result, status = open_prs("graphql", "/repo", (), budget(previous=status, now=RESET))
    assert isinstance(result, Ok) and not status.paused
    assert calls[-2:] == [QUOTA_ARGV, GRAPHQL_ARGV]


def test_quota_drop_during_pagination_discards_partial_result(monkeypatch):
    first = graphql(total=101)
    first["data"]["repository"]["pullRequests"]["pageInfo"] = {"hasNextPage": True, "endCursor": "next"}
    remaining = iter((4000, 2450))
    calls = []

    def run(argv, *_args, **_kwargs):
        calls.append(argv)
        body = quota(next(remaining)) if argv == QUOTA_ARGV else first
        return Ok(Output(0, json.dumps(body)))

    monkeypatch.setattr(github_source.command, "run", run)
    result, status = open_prs("graphql", "/repo", (), budget())
    assert isinstance(result, Err) and status.paused
    assert calls == [QUOTA_ARGV, GRAPHQL_ARGV, QUOTA_ARGV]


@pytest.mark.parametrize("failure", ["graphql", "rate limit", "HTTP 429"])
def test_rate_limit_error_locks_out_until_reset(monkeypatch, failure):
    calls = []
    fail = [True]

    def run(argv, *_args, **_kwargs):
        calls.append(argv)
        if argv == QUOTA_ARGV:
            return Ok(Output(0, json.dumps(quota(4000))))
        if fail[0]:
            if failure == "graphql":
                return Ok(Output(0, json.dumps({"errors": [{"type": "RATE_LIMITED"}]})))
            return Err(CommandFailed("command_failed", argv, 1, failure, "retry"))
        return Ok(Output(0, json.dumps(graphql())))

    monkeypatch.setattr(github_source.command, "run", run)
    result, status = open_prs("graphql", "/repo", (), budget())
    assert isinstance(result, Err) and status.paused and status.rate_limited
    fail[0] = False
    calls.clear()
    result, status = open_prs("graphql", "/repo", (), budget(previous=status, now=RESET - timedelta(seconds=1)))
    assert isinstance(result, Err) and status.paused and calls == []
    result, status = open_prs("graphql", "/repo", (), budget(previous=status, now=RESET))
    assert isinstance(result, Ok) and not status.paused and not status.rate_limited


def test_rest_checks_quota_again_before_check_runs(monkeypatch):
    calls = []
    remaining = iter((4000, 2450))

    def run(argv, *_args, **_kwargs):
        calls.append(argv)
        body = quota(next(remaining), "core") if argv == QUOTA_ARGV else RECORDED_REST
        return Ok(Output(0, json.dumps(body)))

    monkeypatch.setattr(github_source.command, "run", run)
    group = ((RECORDED_REST[0]["head"]["ref"],),)
    result, status = open_prs("rest", "/repo", group, budget("rest"))
    assert isinstance(result, Err) and status.paused
    assert calls == [QUOTA_ARGV, REST_ARGV, QUOTA_ARGV]


def test_invalid_quota_fails_closed(monkeypatch):
    calls = []
    monkeypatch.setattr(github_source.command, "run", lambda argv, *_args, **_kwargs:
                        calls.append(argv) or Ok(Output(0, '{}')))
    result, _ = open_prs("graphql", "/repo", (), budget())
    assert isinstance(result, Err) and result.error.kind == "parse_failed"
    assert calls == [QUOTA_ARGV]


def test_quota_command_failure_identifies_the_failed_command(monkeypatch):
    monkeypatch.setattr(github_source.command, "run", lambda argv, *_args, **_kwargs:
                        Err(CommandFailed("command_failed", argv, 1, "quota unavailable", "retry")))
    result, _ = open_prs("graphql", "/repo", (), budget())
    assert isinstance(result, Err) and isinstance(result.error, CommandFailed)
    assert result.error.argv == QUOTA_ARGV


def test_repeated_pagination_cursor_is_a_typed_failure(monkeypatch):
    page = graphql(total=101)
    page["data"]["repository"]["pullRequests"]["pageInfo"] = {"hasNextPage": True, "endCursor": "same"}
    monkeypatch.setattr(github_source.command, "run", lambda argv, *_args, **_kwargs: Ok(Output(0, json.dumps(
        quota(4000) if argv == QUOTA_ARGV else page))))
    result, _ = open_prs("graphql", "/repo", (), budget())
    assert isinstance(result, Err) and result.error.kind == "parse_failed"
