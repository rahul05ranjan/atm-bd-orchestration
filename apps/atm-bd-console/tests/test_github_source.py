from __future__ import annotations

import json
from pathlib import Path

import pytest

from atm_bd_console import github_source
from atm_bd_console.result import CommandFailed, Err, Ok, Rejected, Timeout, Unreachable


def fake_gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: object, *, exit_code: int = 0, stderr: str = "", sleep: int = 0) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"
    executable = bin_dir / "gh"
    executable.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\" >> \"$FAKE_CALLS\"\nsleep \"${FAKE_SLEEP:-0}\"\nprintf '%s' \"$FAKE_BODY\"\nprintf '%s' \"$FAKE_ERR\" >&2\nexit \"$FAKE_EXIT\"\n")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{__import__('os').environ['PATH']}")
    monkeypatch.setenv("FAKE_CALLS", str(calls))
    monkeypatch.setenv("FAKE_BODY", json.dumps(body))
    monkeypatch.setenv("FAKE_EXIT", str(exit_code))
    monkeypatch.setenv("FAKE_ERR", stderr)
    monkeypatch.setenv("FAKE_SLEEP", str(sleep))
    return calls


def graphql(state: str | None = "SUCCESS", total: int = 1) -> dict:
    rollup = None if state is None else {
        "state": state,
        "contexts": {"nodes": [{"name": "bad", "conclusion": "FAILURE"}]},
    }
    node = {
        "number": 7, "url": "https://x/7", "headRefName": "head", "baseRefName": "base", "headRefOid": "abc",
        "commits": {"nodes": [{"commit": {"statusCheckRollup": rollup}}]},
    }
    return {"data": {"repository": {"pullRequests": {"totalCount": total, "nodes": [node]}}}}


@pytest.mark.parametrize(("state", "expected"), [("SUCCESS", "success"), ("PENDING", "pending"), ("EXPECTED", "pending"), ("FAILURE", "failure"), ("ERROR", "failure"), (None, "none")])
def test_graphql_is_one_call_and_maps_rollups(tmp_path, monkeypatch, state, expected) -> None:
    calls = fake_gh(tmp_path, monkeypatch, graphql(state))
    result, status = github_source.open_prs("graphql")
    assert result.value[0].ci == expected
    assert result.value[0].failed_checks == (() if state is None else ("bad",))
    assert status.ok is True and len(calls.read_text().splitlines()) == 4




def test_failed_check_runs_and_commit_statuses_are_both_named(tmp_path, monkeypatch) -> None:
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
    fake_gh(tmp_path, monkeypatch, body)
    result, _ = github_source.open_prs("graphql")
    assert result.value[0].failed_checks == ("pytest", "slow", "ci/jenkins", "legacy")
    assert "... on StatusContext { context state }" in github_source.GRAPHQL
    assert "status }" not in github_source.GRAPHQL

@pytest.mark.parametrize(("total", "truncated"), [(101, True), (100, False)])
def test_graphql_total_count_over_100_sets_truncated(tmp_path, monkeypatch, total, truncated) -> None:
    fake_gh(tmp_path, monkeypatch, graphql(total=total))
    result, status = github_source.open_prs("graphql")
    assert isinstance(result, Ok) and status.ok is True and status.truncated is truncated

def test_rest_truncation_and_off_make_only_the_allowed_calls(tmp_path, monkeypatch) -> None:
    rows = [{"number": i, "html_url": f"https://x/{i}", "head": {"ref": "h", "sha": "s"}, "base": {"ref": "b"}} for i in range(100)]
    calls = fake_gh(tmp_path, monkeypatch, rows)
    result, status = github_source.open_prs("rest")
    assert len(result.value) == 100 and status.truncated and len(calls.read_text().splitlines()) == 2
    calls.unlink()
    off, off_status = github_source.open_prs("off")
    assert off.value == () and off_status.ok is None and not calls.exists()


@pytest.mark.parametrize(("stderr", "kind", "http"), [
    ("gh: Bad credentials (HTTP 401)", Rejected, 401),
    ("gh: Resource not accessible by integration (HTTP 403)", Rejected, 403),
    ("gh: Bad Gateway (HTTP 502)", Rejected, 502),
    ("gh: Not Found (HTTP 404)", CommandFailed, None),
    ("unknown flag: --5", CommandFailed, None),
    ("error connecting to api.github.com", Unreachable, None),
])
def test_gh_errors_are_typed(tmp_path, monkeypatch, stderr, kind, http) -> None:
    fake_gh(tmp_path, monkeypatch, {}, exit_code=1, stderr=stderr)
    result, status = github_source.open_prs("graphql")
    assert isinstance(result, Err) and isinstance(result.error, kind) and status.ok is False and status.error == result.error
    if http is not None:
        assert result.error.status == http


def test_gh_timeout_is_typed(tmp_path, monkeypatch) -> None:
    fake_gh(tmp_path, monkeypatch, {}, sleep=2)
    monkeypatch.setattr(github_source, "REQUEST_TIMEOUT_S", 1)
    result, status = github_source.open_prs("graphql")
    assert isinstance(result, Err) and isinstance(result.error, Timeout) and status.ok is False
