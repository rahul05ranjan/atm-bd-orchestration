"""In-memory cache of closed beads for one console process; a closed bead never changes."""
from __future__ import annotations

import os
import tempfile
import threading
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .codec import from_json, to_json
from .enums import SprintState
from .result import Err, Error, IoFailed, Ok, ParseFailed, Result
from .types import Bead

STAGES = ("qa", "finding")
FILE_NAME = "closed.json"


@dataclass(frozen=True)
class StageRows:
    phase: str
    stage: str
    rows: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class OpenStage:
    phase: str
    stage: str
    ids: tuple[str, ...]


@dataclass(frozen=True)
class ClosedFile:
    """The persisted cache: closed beads only, so a restart's first tick has nothing closed to read."""

    beads: tuple[Bead, ...]
    rows: tuple[dict[str, Any], ...]
    stage_rows: tuple[StageRows, ...]
    seen_open: tuple[OpenStage, ...]


class ClosedBeads:
    """Closed beads by id. Only closed beads are stored; open beads are always re-read."""

    def __init__(self, directory: Path | None = None) -> None:
        self._directory = directory
        self._dirty = False
        self.pending_errors: list[Error] = []
        self._lock = threading.Lock()
        self._beads: dict[str, Bead] = {}
        self._rows: dict[str, dict[str, Any]] = {}
        self._stage_rows: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
        self._open_stage: dict[tuple[str, str], set[str]] = {}
        self._seen_open_stage: dict[tuple[str, str], set[str]] = {}

    @classmethod
    def load(cls, directory: Path) -> ClosedBeads:
        """Load the persisted cache. A missing file is a cold start; an unreadable or corrupt one is a typed
        error in `pending_errors` and the cache starts empty (the next save replaces the file)."""
        cache = cls(directory)
        path = directory / FILE_NAME
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return cache
        except (OSError, UnicodeDecodeError) as error:
            cache.pending_errors.append(IoFailed("io_failed", str(path), str(error), f"repair or delete {path}; the closed-bead cache starts empty"))
            return cache
        decoded = from_json(ClosedFile, text)
        if isinstance(decoded, Err):
            error = decoded.error
            fix = f"delete {path}; the closed-bead cache starts empty and is rewritten"
            cache.pending_errors.append(replace(error, source=f"{path}:{error.source}", fix=fix) if isinstance(error, ParseFailed) else error)
            return cache
        value = decoded.value
        cache._beads = {b.id: b for b in value.beads if b.status == SprintState.CLOSED}
        cache._rows = {r["id"]: r for r in value.rows if r.get("status") == "closed" and isinstance(r.get("id"), str)}
        cache._stage_rows = {(item.phase, item.stage): {r["id"]: r for r in item.rows if isinstance(r.get("id"), str)}
                             for item in value.stage_rows}
        cache._seen_open_stage = {(item.phase, item.stage): set(item.ids) for item in value.seen_open}
        return cache

    def save(self) -> Result[None]:
        """Write the cache atomically when it changed; a failed write is a typed error and is retried next tick."""
        if self._directory is None:
            return Ok(None)
        path = self._directory / FILE_NAME
        temporary: str | None = None
        with self._lock:
            if not self._dirty:
                return Ok(None)
            payload = ClosedFile(
                tuple(self._beads.values()), tuple(self._rows.values()),
                tuple(StageRows(phase, stage, tuple(rows.values())) for (phase, stage), rows in self._stage_rows.items()),
                tuple(OpenStage(phase, stage, tuple(sorted(ids))) for (phase, stage), ids in self._seen_open_stage.items()),
            )
            try:
                descriptor, temporary = tempfile.mkstemp(prefix=f".{FILE_NAME}.", dir=self._directory)
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(to_json(payload))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
            except (OSError, ValueError) as error:
                if temporary is not None:
                    try:
                        os.unlink(temporary)
                    except OSError:
                        pass
                return Err(IoFailed("io_failed", str(path), str(error), "repair the state directory permissions"))
            self._dirty = False
            return Ok(None)

    def missing_beads(self, ids: Iterable[str]) -> tuple[str, ...]:
        with self._lock:
            return tuple(bead_id for bead_id in ids if bead_id not in self._beads)

    def cached_beads(self, ids: Iterable[str]) -> tuple[Bead, ...]:
        with self._lock:
            return tuple(self._beads[bead_id] for bead_id in ids if bead_id in self._beads)

    def store_beads(self, beads: Iterable[Bead]) -> None:
        with self._lock:
            for bead in beads:
                if bead.status == SprintState.CLOSED:
                    self._beads[bead.id] = bead
                    self._dirty = True

    def forget_beads(self, ids: Iterable[str]) -> None:
        """A bead seen open again (reopened) is no longer closed."""
        with self._lock:
            for bead_id in ids:
                if self._beads.pop(bead_id, None) is not None:
                    self._dirty = True

    def missing_rows(self, ids: Iterable[str]) -> tuple[str, ...]:
        with self._lock:
            return tuple(bead_id for bead_id in ids if bead_id not in self._rows)

    def cached_rows(self, ids: Iterable[str]) -> list[dict[str, Any]]:
        with self._lock:
            return [self._rows[bead_id] for bead_id in ids if bead_id in self._rows]

    def store_rows(self, rows: Iterable[dict[str, Any]]) -> None:
        with self._lock:
            for row in rows:
                if row.get("status") == "closed" and isinstance(row.get("id"), str):
                    self._rows[row["id"]] = row
                    self._dirty = True

    def note_open(self, phase: str, rows: Iterable[dict[str, Any]]) -> None:
        """Record the open QA/finding ids of a successful open read, to find the ones that close later."""
        current: dict[str, set[str]] = {stage: set() for stage in STAGES}
        for row in rows:
            labels = row.get("labels") or []
            for stage in STAGES:
                if f"stage:{stage}" in labels and isinstance(row.get("id"), str):
                    current[stage].add(row["id"])
        with self._lock:
            for stage, ids in current.items():
                self._open_stage[(phase, stage)] = ids
                seen = self._seen_open_stage.setdefault((phase, stage), set())
                if not ids <= seen:
                    seen.update(ids)
                    self._dirty = True

    def stage_rows(self, phase: str, stage: str) -> list[dict[str, Any]] | None:
        with self._lock:
            rows = self._stage_rows.get((phase, stage))
            return None if rows is None else list(rows.values())

    def set_stage_rows(self, phase: str, stage: str, rows: Iterable[dict[str, Any]]) -> None:
        with self._lock:
            self._stage_rows[(phase, stage)] = {row["id"]: row for row in rows}
            self._dirty = True
        self.store_rows(rows)

    def add_stage_rows(self, phase: str, stage: str, rows: Iterable[dict[str, Any]]) -> None:
        with self._lock:
            target = self._stage_rows.setdefault((phase, stage), {})
            for row in rows:
                if row.get("status") == "closed" and isinstance(row.get("id"), str):
                    target[row["id"]] = row
                    self._dirty = True

    def vanished_stage_ids(self, phase: str, stage: str) -> tuple[str, ...]:
        """Ids once seen open that are neither open now nor cached closed: they closed since the list was read."""
        key = (phase, stage)
        with self._lock:
            if key not in self._open_stage:
                return ()  # no open read yet this run: nothing can be called vanished
            gone = self._seen_open_stage.get(key, set()) - self._open_stage.get(key, set()) - set(self._stage_rows.get(key, {}))
            return tuple(sorted(gone))

    def drop_stage_lists(self) -> None:
        """An explicit refresh re-lists the closed stages once; closed beads themselves stay cached."""
        with self._lock:
            self._stage_rows.clear()
            self._dirty = True
