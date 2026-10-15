from __future__ import annotations

import json
from pathlib import Path

import pytest

from atm_bd_console import github_source
from atm_bd_console.result import Err, Rejected, Timeout, Unreachable


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
        "contexts": {"nodes": [{"name": "bad", "conclusion": "FAILURE", "status": "COMPLETED"}]},
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


def test_rest_truncation_and_off_make_only_the_allowed_calls(tmp_path, monkeypatch) -> None:
    rows = [{"number": i, "html_url": f"https://x/{i}", "head": {"ref": "h", "sha": "s"}, "base": {"ref": "b"}} for i in range(100)]
    calls = fake_gh(tmp_path, monkeypatch, rows)
    result, status = github_source.open_prs("rest")
    assert len(result.value) == 100 and status.truncated and len(calls.read_text().splitlines()) == 2
    calls.unlink()
    off, off_status = github_source.open_prs("off")
    assert off.value == () and off_status.ok is None and not calls.exists()


@pytest.mark.parametrize(("stderr", "kind"), [("HTTP 401", Rejected), ("HTTP 502", Rejected), ("error connecting to host", Unreachable)])
def test_gh_errors_are_typed(tmp_path, monkeypatch, stderr, kind) -> None:
    fake_gh(tmp_path, monkeypatch, {}, exit_code=1, stderr=stderr)
    result, status = github_source.open_prs("graphql")
    assert isinstance(result, Err) and isinstance(result.error, kind) and status.ok is False


def test_gh_timeout_is_typed(tmp_path, monkeypatch) -> None:
    fake_gh(tmp_path, monkeypatch, {}, sleep=2)
    monkeypatch.setattr(github_source, "REQUEST_TIMEOUT_S", 1)
    result, status = github_source.open_prs("graphql")
    assert isinstance(result, Err) and isinstance(result.error, Timeout) and status.ok is False
