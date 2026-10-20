"""One shared poll contract: a failed read keeps the last good data, shows what failed since when, and clears on success."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime

from .result import Error
from .types import Snapshot

TICK = "tick"
# Read sources whose failure blanks a part of the snapshot, grouped by what they feed.
PHASE = frozenset({"phase_file", "repo_config", "beads.show_root", "beads.open", "beads.ready", "beads.show_closed"})
AGENTS = frozenset({"atm.members", "atm.tasks", "beads.show_tasks", "beads.task_ancestors"})
WORKTREES = frozenset({"git.worktrees"})
# Dispatch is skipped (so Held and its problems vanish) when any of these fail.
DISPATCH = AGENTS | WORKTREES | {"atm.task_events"}
CARRIED = PHASE | DISPATCH


@dataclass(frozen=True)
class Failure:
    source: str
    error: Error
    since: datetime


class PollHealth:
    """Holds the last good snapshot and the open failures; no I/O, no clock."""

    def __init__(self) -> None:
        self.last_good: Snapshot | None = None
        self.failures: dict[str, Failure] = {}

    def apply(self, snapshot: Snapshot, failed: Mapping[str, Error]) -> Snapshot:
        """Return the snapshot to show: the new one with every failed part replaced by its last good value."""
        now = snapshot.generated_at
        carried = {source: error for source, error in failed.items() if source in CARRIED}
        self._track(carried, now)
        previous = self.last_good
        shown = snapshot
        if previous is not None and carried:
            shown = _carry(previous, snapshot, set(carried), tuple(carried.values()))
        self.last_good = shown
        return shown

    def tick_failed(self, error: Error, now: datetime) -> None:
        """A whole tick raised: the shown snapshot is untouched; only the indicator changes."""
        since = self.failures[TICK].since if TICK in self.failures else now
        self.failures[TICK] = Failure(TICK, error, since)

    def indicator(self) -> str | None:
        if not self.failures:
            return None
        ordered = sorted(self.failures.values(), key=lambda failure: (failure.since, failure.source))
        since = ordered[0].since
        names = ", ".join(failure.source for failure in ordered)
        return f"! {names} failed since {since:%H:%M:%S}"

    def _track(self, carried: Mapping[str, Error], now: datetime) -> None:
        self.failures = {
            source: Failure(source, error, self.failures[source].since if source in self.failures else now)
            for source, error in carried.items()
        }


def _carry(previous: Snapshot, new: Snapshot, sources: set[str], failed_errors: tuple[Error, ...]) -> Snapshot:
    errors = tuple(error for error in new.errors if error not in failed_errors)
    live = {"generated_at": new.generated_at, "gh": new.gh, "errors": errors, "sent": new.sent, "files": new.files}
    if sources & PHASE and previous.phase_data is not None:
        return replace(previous, **live)
    held = {"sections": previous.sections, "problems": previous.problems}
    parts: dict[str, object] = {}
    if sources & AGENTS:
        parts.update(agents=previous.agents, member_data=previous.member_data, task_data=previous.task_data,
                     task_bead_data=previous.task_bead_data, mermaid=previous.mermaid, **held)
    if sources & WORKTREES:
        parts.update(worktree_data=previous.worktree_data, **held)
    if sources & DISPATCH:
        parts.update(held)
    return replace(new, errors=errors, **parts)
