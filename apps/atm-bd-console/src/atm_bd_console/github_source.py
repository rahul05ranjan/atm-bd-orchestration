"""Single-call GitHub pull-request source."""
from __future__ import annotations

import json
import re
import subprocess
from datetime import UTC, datetime

from .result import CommandFailed, Err, Ok, ParseFailed, Rejected, Timeout, Unreachable
from .types import GhStatus, PullRequest

GRAPHQL = """query { repository(owner: \"randlee\", name: \"atm-bd-orchestration\") { pullRequests(states: OPEN, first: 100) { totalCount nodes { number url headRefName baseRefName headRefOid commits(last: 1) { nodes { commit { statusCheckRollup { state contexts(first: 100) { nodes { ... on CheckRun { name conclusion } ... on StatusContext { context state } } } } } } } } } } }"""
FAILURES = {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT"}
REQUEST_TIMEOUT_S = 20


def _status(mode: str, ok: bool | None, error=None, truncated: bool = False) -> GhStatus:
    return GhStatus(mode, ok, error, datetime.now(UTC) if ok is not None else None, truncated)  # type: ignore[arg-type]


def _http_status(stderr: str) -> int | None:
    match = re.search(r"HTTP (\d{3})", stderr)
    return int(match.group(1)) if match else None


def _error(mode: str, argv: tuple[str, ...], code: int, stderr: str):
    lower = stderr.lower()
    if "timed out" in lower or "timeout" in lower:
        error = Timeout("timeout", argv, 20, "check GitHub connectivity and retry")
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
    return PullRequest(node["number"], node["url"], node["headRefName"], node["baseRefName"], node["headRefOid"], _ci(rollup.get("state")), failed)


def open_prs(mode: str):
    if mode == "off":
        return Ok(()), _status("off", None)
    argv = ("gh", "api", "graphql", "-f", f"query={GRAPHQL}") if mode == "graphql" else ("gh", "api", "repos/randlee/atm-bd-orchestration/pulls?state=open&per_page=100")
    try:
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=REQUEST_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired:
        error = Timeout("timeout", argv, REQUEST_TIMEOUT_S, "check GitHub connectivity and retry")
        return Err(error), _status(mode, False, error)
    if completed.returncode:
        return _error(mode, argv, completed.returncode, (completed.stderr or completed.stdout).strip())
    try:
        body = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        error = ParseFailed("github response", str(exc), "retry the GitHub request")
        return Err(error), _status(mode, False, error)
    if mode == "graphql":
        pulls = body["data"]["repository"]["pullRequests"]
        rows = tuple(_from_graphql(node) for node in pulls["nodes"])
        return Ok(rows), _status(mode, True, truncated=pulls["totalCount"] > 100)
    rows = tuple(PullRequest(row["number"], row["html_url"], row["head"]["ref"], row["base"]["ref"], row["head"]["sha"], "none", ()) for row in body)
    return Ok(rows), _status(mode, True, truncated=len(rows) >= 100)
