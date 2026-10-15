from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from typing import Any

from .result import CommandFailed, Err, Ok, ParseFailed, Result
from .types import Bead


def _error(argv: list[str], completed: subprocess.CompletedProcess[str]) -> Err:
    return Err(CommandFailed("command_failed", tuple(argv), completed.returncode, completed.stderr, "inspect bd output"))


def _bead(raw: dict[str, Any]) -> Bead:
    def when(name: str) -> datetime | None:
        value = raw.get(name)
        return datetime.fromisoformat(value).astimezone(UTC) if value else None
    return Bead(raw["id"], raw["title"], raw["status"], tuple(raw.get("labels", ()),), raw.get("parent"), tuple(raw.get("blocked_by", ())), raw.get("assignee"), raw.get("metadata", {}), when("created_at") or datetime.now(UTC), when("claimed_at"), when("closed_at"))


def _run(argv: list[str]) -> Result[tuple[Bead, ...]]:
    completed = subprocess.run(argv, check=False, capture_output=True, text=True)
    if completed.returncode:
        return _error(argv, completed)
    try:
        raw = json.loads(completed.stdout)
        return Ok(tuple(_bead(item) for item in raw))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        return Err(ParseFailed("parse_failed", "bd", str(error), "repair bd JSON"))


def show(ids: tuple[str, ...]) -> Result[tuple[Bead, ...]]:
    return Ok(()) if not ids else _run(["bd", "show", *ids, "--json"])


def open_beads(label: str) -> Result[tuple[Bead, ...]]:
    return _run(["bd", "list", "-l", label, "--json"])


def ready(label: str) -> Result[tuple[Bead, ...]]:
    return _run(["bd", "ready", "-l", label, "--json"])


def ancestors(beads: tuple[Bead, ...]) -> Result[tuple[Bead, ...]]:
    known = {bead.id: bead for bead in beads}
    frontier = {bead.parent for bead in beads if bead.parent and bead.parent not in known}
    while frontier:
        found = show(tuple(sorted(frontier)))
        if isinstance(found, Err):
            return found
        for bead in found.value:
            known[bead.id] = bead
        frontier = {bead.parent for bead in found.value if bead.parent and bead.parent not in known}
    return Ok(tuple(known.values()))
