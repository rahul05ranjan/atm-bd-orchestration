from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from . import command
from .atm_source import sprint_state_of
from .result import Err, Ok, ParseFailed, Result
from .types import Bead


@dataclass(frozen=True)
class BeadRows:
    """One bd list response retained both as typed beads and raw rows for read-only projections."""

    beads: tuple[Bead, ...]
    rows: tuple[dict[str, Any], ...]


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


def _raw(argv: list[str], *, cwd: str | None = None) -> Result[list[dict[str, Any]]]:
    completed = command.run(argv, "inspect bd output", cwd=cwd)
    if isinstance(completed, Err):
        return completed
    try:
        raw = json.loads(completed.value.stdout)
        if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
            raise ValueError("expected an array of bead objects")
        return Ok(raw)
    except (AttributeError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        return Err(ParseFailed("parse_failed", "bd", str(error), "repair bd JSON"))


def _run(argv: list[str], *, cwd: str | None = None) -> Result[tuple[Bead, ...]]:
    raw = _raw(argv, cwd=cwd)
    if isinstance(raw, Err):
        return raw
    try:
        return Ok(tuple(_bead(item) for item in raw.value))
    except (KeyError, TypeError, ValueError) as error:
        return Err(ParseFailed("parse_failed", "bd", str(error), "repair bd JSON"))


def show(ids: tuple[str, ...]) -> Result[tuple[Bead, ...]]:
    return Ok(()) if not ids else _run(["bd", "show", *ids, "--json"])


def open_beads(label: str) -> Result[tuple[Bead, ...]]:
    opened = open_rows(label)
    return opened if isinstance(opened, Err) else Ok(opened.value.beads)


def open_rows(label: str) -> Result[BeadRows]:
    raw = _raw(["bd", "list", "-l", label, "--json"])
    if isinstance(raw, Err):
        return raw
    try:
        return Ok(BeadRows(tuple(_bead(item) for item in raw.value), tuple(raw.value)))
    except (KeyError, TypeError, ValueError) as error:
        return Err(ParseFailed("parse_failed", "bd", str(error), "repair bd JSON"))


def ready(label: str) -> Result[tuple[Bead, ...]]:
    return _run(["bd", "ready", "-l", label, "--json"])


def run_beads(phase: str, stage: str, *, cwd: str | None = None) -> Result[list[dict[str, Any]]]:
    """Return unprojected QA/finding beads, including their dependency edges."""
    rows: dict[str, dict[str, Any]] = {}
    # Default bd list excludes closed beads; explicit status includes history without --all.
    for status_args in ([], ["--status", "closed"]):
        value = _raw(["bd", "list", "-l", f"phase-{phase},stage:{stage}", *status_args, "-n", "0", "--json"], cwd=cwd)
        if isinstance(value, Err):
            return value
        if not all(isinstance(row.get("id"), str) for row in value.value):
            return Err(ParseFailed("parse_failed", "bd", "expected bead objects with ids", "repair bd JSON"))
        rows.update({row["id"]: row for row in value.value})
    return Ok(list(rows.values()))


def raw_show(ids: tuple[str, ...], *, cwd: str | None = None) -> Result[list[dict[str, Any]]]:
    if not ids:
        return Ok([])
    return _raw(["bd", "show", *ids, "--json"], cwd=cwd)


def closed_phase_rows(phase: str, *, cwd: str | None = None) -> Result[list[dict[str, Any]]]:
    """Read the phase's closed rows once for the Quality tab; open rows come from the runner tick."""
    result = _raw(["bd", "list", "-l", f"phase-{phase}", "--status", "closed", "-n", "0", "--json"], cwd=cwd)
    if isinstance(result, Err):
        return result
    if not all(isinstance(row.get("id"), str) for row in result.value):
        return Err(ParseFailed("parse_failed", "bd", "expected bead objects with ids", "repair bd JSON"))
    return result


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
