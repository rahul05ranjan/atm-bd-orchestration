from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta

import pytest
from test_github_source import RECORDED_REST, graphql

from atm_bd_console import github_source
from atm_bd_console.command import Output
from atm_bd_console.result import CommandFailed, Err, Ok
from atm_bd_console.types import GhStatus

NOW = datetime(2026, 10, 18, 12, tzinfo=UTC)
RESET = NOW + timedelta(hours=1)


def quota(remaining, resource="graphql"):
    return {"resources": {resource: {"remaining": remaining, "limit": 5000, "reset": int(RESET.timestamp())}}}


def budget(mode="graphql", previous=None, now=NOW):
    return github_source.Budget(mode, "/repo", 50, 600, now, previous or GhStatus(mode, None, None, None, False))


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
        body = quota(2500) if argv == github_source.QUOTA_ARGV else second if "cursor=page-one" in argv else first
        return Ok(Output(0, json.dumps(body)))

    monkeypatch.setattr(github_source.command, "run", run)
    result, status = github_source.open_prs("graphql", "/repo", (), budget())
    assert isinstance(result, Ok) and len(result.value) == 101
    assert not status.truncated and not status.paused
    assert calls[0] == calls[2] == github_source.QUOTA_ARGV
    assert calls[1] == github_source.GRAPHQL_ARGV and "cursor=page-one" in calls[3]


def test_pauses_at_49_percent_and_recovers_at_threshold(monkeypatch):
    remaining = [2450]
    calls = []

    def run(argv, *_args, **_kwargs):
        calls.append(argv)
        body = quota(remaining[0]) if argv == github_source.QUOTA_ARGV else graphql()
        return Ok(Output(0, json.dumps(body)))

    monkeypatch.setattr(github_source.command, "run", run)
    result, status = github_source.open_prs("graphql", "/repo", (), budget())
    assert isinstance(result, Err) and status.paused and status.quota_remaining == 2450
    assert status.quota_limit == 5000 and status.quota_reset == RESET
    assert calls == [github_source.QUOTA_ARGV]
    remaining[0] = 2500
    result, status = github_source.open_prs("graphql", "/repo", (), budget(previous=status))
    assert isinstance(result, Err) and status.paused and calls == [github_source.QUOTA_ARGV]
    result, status = github_source.open_prs("graphql", "/repo", (), budget(previous=status, now=RESET))
    assert isinstance(result, Ok) and not status.paused
    assert calls[-2:] == [github_source.QUOTA_ARGV, github_source.GRAPHQL_ARGV]


def test_quota_drop_during_pagination_discards_partial_result(monkeypatch):
    first = graphql(total=101)
    first["data"]["repository"]["pullRequests"]["pageInfo"] = {"hasNextPage": True, "endCursor": "next"}
    remaining = iter((4000, 2450))
    calls = []

    def run(argv, *_args, **_kwargs):
        calls.append(argv)
        body = quota(next(remaining)) if argv == github_source.QUOTA_ARGV else first
        return Ok(Output(0, json.dumps(body)))

    monkeypatch.setattr(github_source.command, "run", run)
    result, status = github_source.open_prs("graphql", "/repo", (), budget())
    assert isinstance(result, Err) and status.paused
    assert calls == [github_source.QUOTA_ARGV, github_source.GRAPHQL_ARGV, github_source.QUOTA_ARGV]


@pytest.mark.parametrize("failure", ["graphql", "rate limit", "HTTP 429"])
def test_rate_limit_error_locks_out_until_reset(monkeypatch, failure):
    calls = []
    fail = [True]

    def run(argv, *_args, **_kwargs):
        calls.append(argv)
        if argv == github_source.QUOTA_ARGV:
            return Ok(Output(0, json.dumps(quota(4000))))
        if fail[0]:
            if failure == "graphql":
                return Ok(Output(0, json.dumps({"errors": [{"type": "RATE_LIMITED"}]})))
            return Err(CommandFailed("command_failed", argv, 1, failure, "retry"))
        return Ok(Output(0, json.dumps(graphql())))

    monkeypatch.setattr(github_source.command, "run", run)
    result, status = github_source.open_prs("graphql", "/repo", (), budget())
    assert isinstance(result, Err) and status.paused and status.rate_limited
    fail[0] = False
    calls.clear()
    result, status = github_source.open_prs("graphql", "/repo", (), budget(previous=status, now=RESET - timedelta(seconds=1)))
    assert isinstance(result, Err) and status.paused and calls == []
    result, status = github_source.open_prs("graphql", "/repo", (), budget(previous=status, now=RESET))
    assert isinstance(result, Ok) and not status.paused and not status.rate_limited


def test_rest_checks_quota_again_before_check_runs(monkeypatch):
    calls = []
    remaining = iter((4000, 2450))

    def run(argv, *_args, **_kwargs):
        calls.append(argv)
        body = quota(next(remaining), "core") if argv == github_source.QUOTA_ARGV else RECORDED_REST
        return Ok(Output(0, json.dumps(body)))

    monkeypatch.setattr(github_source.command, "run", run)
    group = ((RECORDED_REST[0]["head"]["ref"],),)
    result, status = github_source.open_prs("rest", "/repo", group, budget("rest"))
    assert isinstance(result, Err) and status.paused
    assert calls == [github_source.QUOTA_ARGV, github_source.REST_ARGV, github_source.QUOTA_ARGV]


def test_invalid_quota_fails_closed(monkeypatch):
    calls = []
    monkeypatch.setattr(github_source.command, "run", lambda argv, *_args, **_kwargs:
                        calls.append(argv) or Ok(Output(0, '{}')))
    result, _ = github_source.open_prs("graphql", "/repo", (), budget())
    assert isinstance(result, Err) and result.error.kind == "parse_failed"
    assert calls == [github_source.QUOTA_ARGV]


def test_quota_command_failure_identifies_the_failed_command(monkeypatch):
    monkeypatch.setattr(github_source.command, "run", lambda argv, *_args, **_kwargs:
                        Err(CommandFailed("command_failed", argv, 1, "quota unavailable", "retry")))
    result, _ = github_source.open_prs("graphql", "/repo", (), budget())
    assert isinstance(result, Err) and isinstance(result.error, CommandFailed)
    assert result.error.argv == github_source.QUOTA_ARGV


def test_repeated_pagination_cursor_is_a_typed_failure(monkeypatch):
    page = graphql(total=101)
    page["data"]["repository"]["pullRequests"]["pageInfo"] = {"hasNextPage": True, "endCursor": "same"}
    monkeypatch.setattr(github_source.command, "run", lambda argv, *_args, **_kwargs: Ok(Output(0, json.dumps(
        quota(4000) if argv == github_source.QUOTA_ARGV else page))))
    result, _ = github_source.open_prs("graphql", "/repo", (), budget())
    assert isinstance(result, Err) and result.error.kind == "parse_failed"
