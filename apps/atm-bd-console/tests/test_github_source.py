from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from atm_bd_console import github_source
from atm_bd_console.result import CommandFailed, Err, Ok, ParseFailed, Rejected, Timeout, Unreachable


def fake_gh(fake_bin, monkeypatch: pytest.MonkeyPatch, body: object, *, exit_code: int = 0, stderr: str = "", sleep: int = 0):
    """A fake gh on the conftest PATH; `fake_bin.argv()` holds one entry per invocation."""
    fake_bin.make("gh", 'sleep "$FAKE_SLEEP"\nprintf \'%s\' "$FAKE_BODY"\nprintf \'%s\' "$FAKE_ERR" >&2\nexit "$FAKE_EXIT"\n')
    monkeypatch.setenv("FAKE_BODY", json.dumps(body))
    monkeypatch.setenv("FAKE_EXIT", str(exit_code))
    monkeypatch.setenv("FAKE_ERR", stderr)
    monkeypatch.setenv("FAKE_SLEEP", str(sleep))
    return fake_bin


# Recorded from randlee/atm-bd-orchestration on 2026-10-15 with GRAPHQL and the REST pulls URL, trimmed to
# PRs #2 (no rollup), #147 (SUCCESS) and #148 (PENDING, one check still running), two contexts each.
FIXTURES = Path(__file__).parent / "fixtures" / "github"
RECORDED_GRAPHQL = json.loads((FIXTURES / "graphql-open-prs.json").read_text())
RECORDED_REST = json.loads((FIXTURES / "rest-open-prs.json").read_text())


def graphql(state: str | None = "SUCCESS", total: int = 1) -> dict:
    """The recorded #147 node with its rollup state set (None removes the rollup) and one failed check."""
    body = copy.deepcopy(RECORDED_GRAPHQL)
    pulls = body["data"]["repository"]["pullRequests"]
    node = next(item for item in pulls["nodes"] if item["number"] == 147)
    commit = node["commits"]["nodes"][-1]["commit"]
    if state is None:
        commit["statusCheckRollup"] = None
    else:
        commit["statusCheckRollup"]["state"] = state
        commit["statusCheckRollup"]["contexts"]["nodes"] = [{"name": "bad", "conclusion": "FAILURE"}]
    pulls["nodes"], pulls["totalCount"] = [node], total
    return body


def test_recorded_graphql_maps_every_pull_request(fake_bin, monkeypatch) -> None:
    fake_gh(fake_bin, monkeypatch, RECORDED_GRAPHQL)
    result, status = github_source.open_prs("graphql")
    assert isinstance(result, Ok) and status.ok is True and status.truncated is False
    assert [(pr.number, pr.head, pr.base, pr.ci, pr.failed_checks) for pr in result.value] == [
        (2, "feat/four-job-beads-workflow", "main", "none", ()),
        (147, "sprint/bh-10-textual-tabs", "fix/bh-4-sanity-r4", "success", ()),
        (148, "fix/bh-5-qa-r1", "sprint/bh-10-textual-tabs", "pending", ()),
    ]
    assert result.value[2].head_sha == "481eba418048db6c206bd5c043e29ce216b731ef"
    assert result.value[2].url == "https://github.com/randlee/atm-bd-orchestration/pull/148"


def test_recorded_rest_maps_every_pull_request_without_ci(fake_bin, monkeypatch) -> None:
    fake_gh(fake_bin, monkeypatch, RECORDED_REST)
    result, status = github_source.open_prs("rest")
    assert isinstance(result, Ok) and status.ok is True and status.truncated is False
    assert [(pr.number, pr.head, pr.base, pr.head_sha, pr.ci) for pr in result.value] == [
        (row["number"], row["head"]["ref"], row["base"]["ref"], row["head"]["sha"], "none") for row in RECORDED_REST
    ]
    assert {pr.number for pr in result.value} == {2, 147, 148}


@pytest.mark.parametrize(("state", "expected"), [("SUCCESS", "success"), ("PENDING", "pending"), ("EXPECTED", "pending"), ("FAILURE", "failure"), ("ERROR", "failure"), (None, "none")])
def test_graphql_is_one_call_and_maps_rollups(fake_bin, monkeypatch, state, expected) -> None:
    calls = fake_gh(fake_bin, monkeypatch, graphql(state))
    result, status = github_source.open_prs("graphql")
    assert result.value[0].ci == expected
    assert result.value[0].failed_checks == (() if state is None else ("bad",))
    assert status.ok is True and [argv[:3] for argv in calls.argv()] == [("gh", "api", "graphql")]




def test_failed_check_runs_and_commit_statuses_are_both_named(fake_bin, monkeypatch) -> None:
    body = graphql("FAILURE")
    rollup = body["data"]["repository"]["pullRequests"]["nodes"][0]["commits"]["nodes"][0]["commit"]["statusCheckRollup"]
    rollup["contexts"]["nodes"] = [
        {"name": "pytest", "conclusion": "FAILURE"},
        {"name": "ruff", "conclusion": "SUCCESS"},
        {"name": "slow", "conclusion": "TIMED_OUT"},
        {"context": "ci/jenkins", "state": "ERROR"},
        {"context": "deploy", "state": "SUCCESS"},
        {"context": "legacy", "state": "FAILURE"},
    ]
    fake_gh(fake_bin, monkeypatch, body)
    result, _ = github_source.open_prs("graphql")
    assert result.value[0].failed_checks == ("pytest", "slow", "ci/jenkins", "legacy")
    assert "... on StatusContext { context state }" in github_source.GRAPHQL
    assert "status }" not in github_source.GRAPHQL

@pytest.mark.parametrize(("total", "truncated"), [(101, True), (100, False)])
def test_graphql_total_count_over_100_sets_truncated(fake_bin, monkeypatch, total, truncated) -> None:
    fake_gh(fake_bin, monkeypatch, graphql(total=total))
    result, status = github_source.open_prs("graphql")
    assert isinstance(result, Ok) and status.ok is True and status.truncated is truncated

def test_rest_truncation_and_off_make_only_the_allowed_calls(fake_bin, monkeypatch) -> None:
    rows = [{**RECORDED_REST[0], "number": i} for i in range(100)]
    calls = fake_gh(fake_bin, monkeypatch, rows)
    result, status = github_source.open_prs("rest")
    assert len(result.value) == 100 and status.truncated
    assert calls.argv() == [("gh", "api", "repos/randlee/atm-bd-orchestration/pulls?state=open&per_page=100")]
    calls.log.unlink()
    off, off_status = github_source.open_prs("off")
    assert off.value == () and off_status.ok is None and calls.argv() == []


@pytest.mark.parametrize(("stderr", "kind", "http"), [
    ("gh: Bad credentials (HTTP 401)", Rejected, 401),
    ("gh: Resource not accessible by integration (HTTP 403)", Rejected, 403),
    ("gh: Bad Gateway (HTTP 502)", Rejected, 502),
    ("gh: Not Found (HTTP 404)", CommandFailed, None),
    ("unknown flag: --5", CommandFailed, None),
    ("error connecting to api.github.com", Unreachable, None),
])
def test_gh_errors_are_typed(fake_bin, monkeypatch, stderr, kind, http) -> None:
    fake_gh(fake_bin, monkeypatch, {}, exit_code=1, stderr=stderr)
    result, status = github_source.open_prs("graphql")
    assert isinstance(result, Err) and isinstance(result.error, kind) and status.ok is False and status.error == result.error
    if http is not None:
        assert result.error.status == http


def test_rest_failure_is_typed(fake_bin, monkeypatch) -> None:
    fake_gh(fake_bin, monkeypatch, {}, exit_code=1, stderr="gh: Resource not accessible by integration (HTTP 403)")
    result, status = github_source.open_prs("rest")
    assert isinstance(result, Err) and result.error == Rejected(
        "rejected", calls_argv(fake_bin), 403, "gh: Resource not accessible by integration (HTTP 403)",
        "check GitHub credentials or retry")
    assert (status.mode, status.ok, status.error) == ("rest", False, result.error)


def calls_argv(fake_bin) -> tuple[str, ...]:
    (argv,) = fake_bin.argv()
    return argv


@pytest.mark.parametrize("mode", ["graphql", "rest"])
@pytest.mark.parametrize("body", [
    "not json",
    {"data": None},
    {"data": {"repository": {"pullRequests": {"totalCount": 1, "nodes": [{"number": 7}]}}}},
    [{"number": 7, "head": "fix/x"}],
    [None],
    {"message": "API rate limit exceeded"},
], ids=["bad-json", "null-data", "node-missing-keys", "head-not-object", "null-row", "error-object"])
def test_unparseable_body_is_parse_failed_and_never_raises(fake_bin, monkeypatch, mode, body) -> None:
    fake_gh(fake_bin, monkeypatch, body)
    if body == "not json":
        monkeypatch.setenv("FAKE_BODY", body)
    result, status = github_source.open_prs(mode)
    assert isinstance(result, Err) and isinstance(result.error, ParseFailed)
    assert result.error.kind == "parse_failed" and result.error.source == f"github {mode} response"
    assert (status.ok, status.error) == (False, result.error)


def test_gh_timeout_is_typed(fake_bin, monkeypatch) -> None:
    fake_gh(fake_bin, monkeypatch, {}, sleep=2)
    monkeypatch.setattr(github_source, "REQUEST_TIMEOUT_S", 1)
    result, status = github_source.open_prs("graphql")
    assert isinstance(result, Err) and isinstance(result.error, Timeout) and status.ok is False
