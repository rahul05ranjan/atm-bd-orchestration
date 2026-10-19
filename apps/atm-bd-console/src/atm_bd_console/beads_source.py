from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from . import command
from .atm_source import sprint_state_of
from .result import Err, Ok, ParseFailed, Result
from .types import Bead


def _bead(raw: dict[str, Any]) -> Bead:
    def when(name: str) -> datetime | None:
        value = raw.get(name)
        return datetime.fromisoformat(value).astimezone(UTC) if value else None
    close_reason = raw.get("close_reason")
    return Bead(
        raw["id"], raw["title"], sprint_state_of(raw["status"]), tuple(raw.get("labels", ())), raw.get("parent"),
        tuple(raw.get("blocked_by", ())), raw.get("assignee"), raw.get("metadata", {}),
        when("created_at") or datetime.now(UTC), when("claimed_at"), when("closed_at"),
        close_reason if isinstance(close_reason, str) else None, raw["status"],
    )


def _run(argv: list[str]) -> Result[tuple[Bead, ...]]:
    completed = command.run(argv, "inspect bd output")
    if isinstance(completed, Err):
        return completed
    try:
        raw = json.loads(completed.value.stdout)
        return Ok(tuple(_bead(item) for item in raw))
    except (AttributeError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        return Err(ParseFailed("parse_failed", "bd", str(error), "repair bd JSON"))


def show(ids: tuple[str, ...]) -> Result[tuple[Bead, ...]]:
    return Ok(()) if not ids else _run(["bd", "show", *ids, "--json"])


def open_beads(label: str) -> Result[tuple[Bead, ...]]:
    return _run(["bd", "list", "-l", label, "--json"])


def ready(label: str) -> Result[tuple[Bead, ...]]:
    return _run(["bd", "ready", "-l", label, "--json"])


def run_beads(phase: str, stage: str, *, cwd: str | None = None) -> Result[list[dict[str, Any]]]:
    """Return unprojected QA/finding beads, including their dependency edges."""
    rows: dict[str, dict[str, Any]] = {}
    # Default bd list excludes closed beads; explicit status includes history without --all.
    for status_args in ([], ["--status", "closed"]):
        completed = command.run(["bd", "list", "-l", f"phase-{phase},stage:{stage}", *status_args, "-n", "0", "--json"],
                                "inspect bd run output", cwd=cwd)
        if isinstance(completed, Err):
            return completed
        try:
            value = json.loads(completed.value.stdout)
            if not isinstance(value, list) or not all(isinstance(row, dict) and isinstance(row.get("id"), str) for row in value):
                raise ValueError("expected an array of bead objects with ids")
            rows.update({row["id"]: row for row in value})
        except ValueError as error:
            return Err(ParseFailed("parse_failed", "bd", str(error), "repair bd JSON"))
    return Ok(list(rows.values()))


def raw_show(ids: tuple[str, ...], *, cwd: str | None = None) -> Result[list[dict[str, Any]]]:
    if not ids:
        return Ok([])
    completed = command.run(["bd", "show", *ids, "--json"], "inspect checked beads", cwd=cwd)
    if isinstance(completed, Err):
        return completed
    try:
        value = json.loads(completed.value.stdout)
        if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
            raise ValueError("expected an array of bead objects")
        return Ok(value)
    except ValueError as error:
        return Err(ParseFailed("parse_failed", "bd", str(error), "repair bd JSON"))


def ancestors(beads: tuple[Bead, ...]) -> Result[tuple[Bead, ...]]:
    known = {bead.id: bead for bead in beads}
    frontier = {bead.parent for bead in beads if bead.parent and bead.parent not in known}
    visited: set[str] = set()
    while frontier:
        visited.update(frontier)
        found = show(tuple(sorted(frontier)))
        if isinstance(found, Err):
            return found
        for bead in found.value:
            known[bead.id] = bead
        frontier = {bead.parent for bead in found.value if bead.parent and bead.parent not in known
                    and bead.parent not in visited}
    return Ok(tuple(known.values()))
