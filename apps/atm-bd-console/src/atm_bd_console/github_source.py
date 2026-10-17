"""Single-call GitHub pull-request source."""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from . import command
from .result import CommandFailed, Err, Ok, ParseFailed, Rejected, Timeout, Unreachable
from .types import GhStatus, PullRequest

GRAPHQL = """query($owner: String!, $name: String!) { repository(owner: $owner, name: $name) { pullRequests(states: [OPEN, CLOSED, MERGED], first: 100, orderBy: {field: CREATED_AT, direction: DESC}) { totalCount nodes { number url state headRefName baseRefName headRefOid commits(last: 1) { nodes { commit { statusCheckRollup { state contexts(first: 100) { nodes { ... on CheckRun { name conclusion } ... on StatusContext { context state } } } } } } } } } } }"""
# The repository is the checkout gh runs in: gh fills {owner} and {repo} from it (or from GH_REPO).
GRAPHQL_ARGV = ("gh", "api", "graphql", "-F", "owner={owner}", "-F", "name={repo}", "-f", f"query={GRAPHQL}")
REST_ARGV = ("gh", "api", "repos/{owner}/{repo}/pulls?state=all&per_page=100")
FAILURES = {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT"}
REQUEST_TIMEOUT_S = 20


def _status(mode: Literal["graphql", "rest", "off"], ok: bool | None, error=None, truncated: bool = False) -> GhStatus:
    return GhStatus(mode, ok, error, datetime.now(UTC) if ok is not None else None, truncated)


def _http_status(stderr: str) -> int | None:
    match = re.search(r"HTTP (\d{3})", stderr)
    return int(match.group(1)) if match else None


def _error(mode: Literal["graphql", "rest", "off"], argv: tuple[str, ...], code: int, stderr: str):
    lower = stderr.lower()
    if "timed out" in lower or "timeout" in lower:
        error = Timeout("timeout", argv, REQUEST_TIMEOUT_S, "check GitHub connectivity and retry")
    elif (status := _http_status(stderr)) is not None and (status in (401, 403) or 500 <= status <= 599):
        error = Rejected("rejected", argv, status, stderr, "check GitHub credentials or retry")
    elif "connect" in lower or "dns" in lower or "network" in lower:
        error = Unreachable("unreachable", argv, stderr, "check network connectivity and retry")
    else:
        error = CommandFailed("command_failed", argv, code, stderr, "check gh and retry")
    return Err(error), _status(mode, False, error)


def _ci(value: str | None) -> str:
    return {"SUCCESS": "success", "PENDING": "pending", "EXPECTED": "pending", "FAILURE": "failure", "ERROR": "failure"}.get(value or "", "none")


def _from_graphql(node: dict) -> PullRequest:
    rollup = (((node.get("commits") or {}).get("nodes") or [{}])[-1].get("commit") or {}).get("statusCheckRollup") or {}
    contexts = (rollup.get("contexts") or {}).get("nodes") or []
    # CheckRun reports name/conclusion, StatusContext (commit statuses) context/state.
    failed = tuple(str(row.get("name") or row.get("context")) for row in contexts
                   if row.get("conclusion") in FAILURES or row.get("state") in FAILURES)
    return PullRequest(node["number"], node["url"], node["headRefName"], node["baseRefName"], node["headRefOid"], _ci(rollup.get("state")), failed, node.get("state", "OPEN").lower())


def open_prs(mode: Literal["graphql", "rest", "off"], repo_root: str | Path | None = None):
    """Open pull requests of the repository checked out at `repo_root` (default: the current directory)."""
    if mode == "off":
        return Ok(()), _status("off", None)
    argv = GRAPHQL_ARGV if mode == "graphql" else REST_ARGV
    completed = command.run(argv, "check GitHub connectivity and retry", cwd=repo_root, timeout_s=REQUEST_TIMEOUT_S)
    if isinstance(completed, Err):
        failure = completed.error
        if isinstance(failure, CommandFailed):
            return _error(mode, argv, failure.code, failure.stderr)
        return completed, _status(mode, False, failure)
    try:
        body = json.loads(completed.value.stdout)
        if mode == "graphql":
            pulls = body["data"]["repository"]["pullRequests"]
            rows = tuple(_from_graphql(node) for node in pulls["nodes"])
            truncated = pulls["totalCount"] > 100
        else:
            rows = tuple(PullRequest(row["number"], row["html_url"], row["head"]["ref"], row["base"]["ref"], row["head"]["sha"], "none", (), "merged" if row.get("merged_at") else row["state"]) for row in body)
            truncated = len(rows) >= 100
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError, IndexError) as exc:
        # A body that is not JSON, or JSON of the wrong shape, is one typed failure; GitHub failing never stops work.
        error = ParseFailed("parse_failed", f"github {mode} response", f"{type(exc).__name__}: {exc}", "retry the GitHub request")
        return Err(error), _status(mode, False, error)
    return Ok(rows), _status(mode, True, truncated=truncated)
