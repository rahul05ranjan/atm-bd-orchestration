"""Single-call GitHub pull-request source."""
from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from . import command
from .ci import ci_failure_key
from .enums import CiState, PrState
from .result import CommandFailed, Err, Ok, ParseFailed, Rejected, Timeout, Unreachable
from .types import GhStatus, PullRequest

GRAPHQL = """query($owner: String!, $name: String!) { repository(owner: $owner, name: $name) { pullRequests(states: [OPEN, CLOSED, MERGED], first: 100, orderBy: {field: CREATED_AT, direction: DESC}) { totalCount nodes { number url state isDraft headRefName baseRefName headRefOid commits(last: 1) { nodes { commit { statusCheckRollup { state contexts(first: 100) { totalCount nodes { ... on CheckRun { name conclusion } ... on StatusContext { context state } } } } } } } } } } }"""
# The repository is the checkout gh runs in: gh fills {owner} and {repo} from it (or from GH_REPO).
GRAPHQL_ARGV = ("gh", "api", "graphql", "-F", "owner={owner}", "-F", "name={repo}", "-f", f"query={GRAPHQL}")
REST_ARGV = ("gh", "api", "repos/{owner}/{repo}/pulls?state=all&per_page=100")
FAILURES = {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT"}
REQUEST_TIMEOUT_S = 20


def ci_state_of(conclusion: str | None, status: str | None, failed_checks: tuple[str, ...]) -> CiState:
    conclusion = conclusion.upper() if conclusion else None
    status = status.upper() if status else None
    if failed_checks or conclusion in FAILURES:
        return ci_failure_key(failed_checks)
    if conclusion not in (None, "", "PENDING", "EXPECTED", "SUCCESS", "SKIPPED", "NEUTRAL"):
        return CiState.UNKNOWN
    if status not in (None, "", "COMPLETED", "QUEUED", "IN_PROGRESS", "PENDING", "WAITING", "REQUESTED"):
        return CiState.UNKNOWN
    if conclusion in ("PENDING", "EXPECTED") or status in ("QUEUED", "IN_PROGRESS", "PENDING", "WAITING", "REQUESTED"):
        return CiState.RUNNING
    if conclusion == "SUCCESS":
        return CiState.PASS
    if conclusion in (None, "", "SKIPPED", "NEUTRAL"):
        if status not in (None, "", "COMPLETED"):
            return CiState.UNKNOWN
        return CiState.NONE
    return CiState.UNKNOWN


def pr_state_of(state: str, is_draft: bool) -> PrState:
    if state.upper() == "OPEN":
        return PrState.DRAFT if is_draft else PrState.OPEN
    if state.upper() == "MERGED":
        return PrState.MERGED
    if state.upper() == "CLOSED":
        return PrState.CLOSED
    return PrState.UNKNOWN


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


def _from_graphql(node: dict) -> PullRequest:
    rollup = (((node.get("commits") or {}).get("nodes") or [{}])[-1].get("commit") or {}).get("statusCheckRollup") or {}
    contexts = (rollup.get("contexts") or {}).get("nodes") or []
    # CheckRun reports name/conclusion, StatusContext (commit statuses) context/state.
    failed = tuple(str(row.get("name") or row.get("context")) for row in contexts
                   if row.get("conclusion") in FAILURES or row.get("state") in FAILURES)
    state = pr_state_of(node.get("state", "OPEN"), bool(node.get("isDraft")))
    raw_ci = rollup.get("state") or ""
    raw_state = node.get("state", "OPEN")
    return PullRequest(node["number"], node["url"], node["headRefName"], node["baseRefName"], node["headRefOid"],
                       ci_state_of(raw_ci, None, failed), failed, state, raw_ci, raw_state)


def open_prs(mode: Literal["graphql", "rest", "off"], repo_root: str | Path | None = None,
             branch_groups: tuple[tuple[str, ...], ...] = ()):
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
            for node in pulls["nodes"]:
                commits = (node.get("commits") or {}).get("nodes") or []
                rollup = ((commits[-1].get("commit") or {}).get("statusCheckRollup") or {}) if commits else {}
                contexts = rollup.get("contexts") or {}
                truncated = truncated or contexts.get("totalCount", len(contexts.get("nodes") or [])) > len(contexts.get("nodes") or [])
        else:
            rows = tuple(PullRequest(row["number"], row["html_url"], row["head"]["ref"], row["base"]["ref"], row["head"]["sha"], CiState.NONE, (), pr_state_of("MERGED" if row.get("merged_at") else row["state"], bool(row.get("draft"))), "", row["state"]) for row in body)
            truncated = len(rows) >= 100
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError, IndexError) as exc:
        # A body that is not JSON, or JSON of the wrong shape, is one typed failure; GitHub failing never stops work.
        error = ParseFailed("parse_failed", f"github {mode} response", f"{type(exc).__name__}: {exc}", "retry the GitHub request")
        return Err(error), _status(mode, False, error)
    if mode == "rest" and branch_groups:
        selected: dict[int, PullRequest] = {}
        priority = {PrState.OPEN: 2, PrState.DRAFT: 2, PrState.MERGED: 1, PrState.CLOSED: 0}
        for branches in branch_groups:
            matching = (pr for pr in rows if pr.head in branches)
            pr = max(matching, key=lambda value: (priority.get(value.state, -1), value.number), default=None)
            if pr is not None:
                selected[pr.number] = pr
        updates: dict[int, PullRequest] = {}
        for pr in selected.values():
            checks_argv = ("gh", "api", f"repos/{{owner}}/{{repo}}/commits/{pr.head_sha}/check-runs?per_page=100")
            response = command.run(checks_argv, "check GitHub connectivity and retry", cwd=repo_root,
                                   timeout_s=REQUEST_TIMEOUT_S)
            if isinstance(response, Err):
                return response, _status(mode, False, response.error)
            try:
                checks = json.loads(response.value.stdout)
                runs = checks["check_runs"]
                truncated = truncated or checks["total_count"] > len(runs)
                failed = tuple(str(run["name"]) for run in runs if (run.get("conclusion") or "").upper() in FAILURES)
                statuses = tuple((run.get("status") or "").upper() for run in runs)
                unexpected_status = next((status for status in statuses if status not in (
                    "", "COMPLETED", "QUEUED", "IN_PROGRESS", "PENDING", "WAITING", "REQUESTED")), "")
                running = any(status in ("QUEUED", "IN_PROGRESS", "PENDING", "WAITING", "REQUESTED")
                              for status in statuses)
                success = any((run.get("conclusion") or "").upper() == "SUCCESS" for run in runs)
                unexpected = next((str(run.get("conclusion")) for run in runs
                                   if run.get("conclusion") and str(run["conclusion"]).upper()
                                   not in (*FAILURES, "SUCCESS", "SKIPPED", "NEUTRAL")), "")
                ci = ci_state_of(unexpected or ("SUCCESS" if success else None),
                                 unexpected_status or ("IN_PROGRESS" if running else None), failed)
                updates[pr.number] = replace(pr, ci=ci, failed_checks=failed, raw_ci=unexpected or unexpected_status)
            except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as exc:
                error = ParseFailed("parse_failed", "github rest check-runs", str(exc), "retry the GitHub request")
                return Err(error), _status(mode, False, error)
        rows = tuple(updates.get(pr.number, pr) for pr in rows)
    return Ok(rows), _status(mode, True, truncated=truncated)
