from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from atm_bd_console import github_source
from atm_bd_console.command import Output
from atm_bd_console.enums import CiState, PrState
from atm_bd_console.result import CommandFailed, Err, Ok, ParseFailed, Rejected, Timeout, Unreachable


def fake_gh(fake_bin, monkeypatch: pytest.MonkeyPatch, body: object, *, exit_code: int = 0, stderr: str = ""):
    """A fake gh on the conftest PATH; `fake_bin.argv()` holds one entry per invocation."""
    fake_bin.make("gh", 'printf \'%s\' "$FAKE_BODY"\nprintf \'%s\' "$FAKE_ERR" >&2\nexit "$FAKE_EXIT"\n')
    monkeypatch.setenv("FAKE_BODY", json.dumps(body))
    monkeypatch.setenv("FAKE_EXIT", str(exit_code))
    monkeypatch.setenv("FAKE_ERR", stderr)
    return fake_bin


@pytest.mark.parametrize(("conclusion", "status", "checks", "expected"), [
    ("FAILURE", "COMPLETED", ("macos-14",), CiState.FAIL_MACOS),
    ("CANCELLED", "COMPLETED", ("darwin",), CiState.FAIL_MACOS),
    ("FAILURE", "COMPLETED", ("windows",), CiState.FAIL_WINDOWS),
    ("FAILURE", "COMPLETED", ("win-x64",), CiState.FAIL_WINDOWS),
    ("TIMED_OUT", "COMPLETED", ("win_x64",), CiState.FAIL_WINDOWS),
    ("FAILURE", "COMPLETED", ("ubuntu",), CiState.FAIL_LINUX),
    ("FAILURE", "COMPLETED", ("linux",), CiState.FAIL_LINUX),
    ("FAILURE", "COMPLETED", ("linux", "macos"), CiState.FAIL_BUG),
    ("FAILURE", "COMPLETED", ("linux", "lint"), CiState.FAIL_BUG),
    ("SKIPPED", "COMPLETED", (), CiState.NONE),
    ("NEUTRAL", "COMPLETED", (), CiState.NONE),
    ("PENDING", "IN_PROGRESS", ("windows",), CiState.FAIL_WINDOWS),
])
def test_ci_classification(conclusion, status, checks, expected):
    assert github_source.ci_state_of(conclusion, status, checks) is expected


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
        (2, "feat/four-job-beads-workflow", "main", CiState.NONE, ()),
        (147, "sprint/bh-10-textual-tabs", "fix/bh-4-sanity-r4", CiState.PASS, ()),
        (148, "fix/bh-5-qa-r1", "sprint/bh-10-textual-tabs", CiState.RUNNING, ()),
    ]
    assert result.value[2].head_sha == "481eba418048db6c206bd5c043e29ce216b731ef"
    assert result.value[2].url == "https://github.com/randlee/atm-bd-orchestration/pull/148"


def test_recorded_rest_maps_every_pull_request_without_ci(fake_bin, monkeypatch) -> None:
    fake_gh(fake_bin, monkeypatch, RECORDED_REST)
    result, status = github_source.open_prs("rest")
    assert isinstance(result, Ok) and status.ok is True and status.truncated is False
    assert [(pr.number, pr.head, pr.base, pr.head_sha, pr.ci) for pr in result.value] == [
        (row["number"], row["head"]["ref"], row["base"]["ref"], row["head"]["sha"], CiState.NONE) for row in RECORDED_REST
    ]
    assert {pr.number for pr in result.value} == {2, 147, 148}


def test_graphql_truncated_check_contexts_make_status_unknown(fake_bin, monkeypatch):
    body = graphql()
    contexts = body["data"]["repository"]["pullRequests"]["nodes"][0]["commits"]["nodes"][-1]["commit"]["statusCheckRollup"]["contexts"]
    contexts["totalCount"] = len(contexts["nodes"]) + 1
    fake_gh(fake_bin, monkeypatch, body)
    result, status = github_source.open_prs("graphql")
    assert isinstance(result, Ok)
    assert status.truncated is True


@pytest.mark.parametrize("state", ("SUCCESS", "PENDING", "EXPECTED", "FAILURE", "ERROR", None))
def test_graphql_is_one_call_and_maps_rollups(fake_bin, monkeypatch, state) -> None:
    calls = fake_gh(fake_bin, monkeypatch, graphql(state))
    result, status = github_source.open_prs("graphql")
    assert result.value[0].ci == (CiState.FAIL_BUG if state is not None else CiState.NONE)
    assert result.value[0].failed_checks == (() if state is None else ("bad",))
    assert status.ok is True and calls.argv() == [github_source.GRAPHQL_ARGV]


def test_rest_fetches_only_selected_pr_checks_and_failure_outranks_running(monkeypatch):
    calls = []
    pull_rows = copy.deepcopy(RECORDED_REST)
    selected = pull_rows[0]
    lower = copy.deepcopy(selected)
    lower["number"] = selected["number"] - 1
    lower["head"]["sha"] = "lower-sha"
    pull_rows.append(lower)

    def run(argv, *_args, **_kwargs):
        calls.append(argv)
        if argv == github_source.REST_ARGV:
            return Ok(Output(0, json.dumps(pull_rows)))
        return Ok(Output(0, json.dumps({"total_count": 2, "check_runs": [
            {"name": "windows", "conclusion": "failure", "status": "completed"},
            {"name": "pending", "conclusion": None, "status": "in_progress"},
        ]})))

    monkeypatch.setattr(github_source.command, "run", run)
    result, status = github_source.open_prs("rest", "/repo", ((selected["head"]["ref"],),))
    assert isinstance(result, Ok) and status.ok
    assert len(calls) == 2
    assert selected["head"]["sha"] in calls[1][-1]
    selected_result = next(pr for pr in result.value if pr.number == selected["number"])
    assert selected_result.ci == CiState.FAIL_WINDOWS
    assert selected_result.failed_checks == ("windows",)


def test_graphql_requests_newest_prs_before_the_100_row_limit() -> None:
    assert "orderBy: {field: CREATED_AT, direction: DESC}" in github_source.GRAPHQL


def test_graphql_and_rest_map_drafts_to_draft_state(fake_bin, monkeypatch) -> None:
    graph = graphql()
    graph["data"]["repository"]["pullRequests"]["nodes"][0]["isDraft"] = True
    fake_gh(fake_bin, monkeypatch, graph)
    assert github_source.open_prs("graphql")[0].value[0].state == PrState.DRAFT
    rest = copy.deepcopy(RECORDED_REST[:1])
    rest[0]["draft"] = True
    fake_gh(fake_bin, monkeypatch, rest)
    assert github_source.open_prs("rest")[0].value[0].state == PrState.DRAFT


def test_graphql_closed_draft_remains_closed(fake_bin, monkeypatch) -> None:
    graph = graphql()
    node = graph["data"]["repository"]["pullRequests"]["nodes"][0]
    node.update(state="CLOSED", isDraft=True)
    fake_gh(fake_bin, monkeypatch, graph)
    assert github_source.open_prs("graphql")[0].value[0].state == PrState.CLOSED


def test_rest_closed_draft_remains_closed(fake_bin, monkeypatch) -> None:
    rest = copy.deepcopy(RECORDED_REST[:1])
    rest[0].update(state="closed", draft=True)
    fake_gh(fake_bin, monkeypatch, rest)
    assert github_source.open_prs("rest")[0].value[0].state == PrState.CLOSED




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
    assert calls.argv() == [("gh", "api", "repos/{owner}/{repo}/pulls?state=all&per_page=100")]
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
    # gh blocks until the runner kills it: the timeout always expires, whatever the host load.
    fake_bin.make("gh", "exec sleep 3600\n")
    monkeypatch.setattr(github_source, "REQUEST_TIMEOUT_S", 1)
    result, status = github_source.open_prs("graphql")
    assert isinstance(result, Err) and result.error == Timeout(
        "timeout", github_source.GRAPHQL_ARGV, 1, "check GitHub connectivity and retry")
    assert (status.ok, status.error) == (False, result.error)


def test_gh_reported_timeout_carries_the_request_timeout(fake_bin, monkeypatch) -> None:
    fake_gh(fake_bin, monkeypatch, {}, exit_code=1, stderr="gh: request timed out")
    monkeypatch.setattr(github_source, "REQUEST_TIMEOUT_S", 7)
    result, status = github_source.open_prs("graphql")
    assert isinstance(result, Err) and isinstance(result.error, Timeout) and result.error.seconds == 7
    assert (status.ok, status.error) == (False, result.error)


def test_no_repository_slug_is_hard_coded(fake_bin, monkeypatch, tmp_path) -> None:
    source = (Path(github_source.__file__)).read_text()
    assert "randlee" not in source and "atm-bd-orchestration" not in source
    assert github_source.GRAPHQL_ARGV[3:7] == ("-F", "owner={owner}", "-F", "name={repo}")
    assert "repository(owner: $owner, name: $name)" in github_source.GRAPHQL
    fake_bin.make("gh", 'printf "%s" "$PWD" >&2\nexit 1\n')
    result, _ = github_source.open_prs("rest", tmp_path)
    assert isinstance(result, Err) and result.error.stderr == str(tmp_path)
