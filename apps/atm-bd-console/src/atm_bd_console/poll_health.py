"""One shared poll contract: a failed read keeps the last good data, shows what failed since when, and clears on success."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime

from .result import Error
from .timefmt import _age, clock
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
    good_at: datetime | None = None  # when the part last read good; None: nothing was ever carried


class PollHealth:
    """Holds the last good snapshot and the open failures; no I/O, no clock."""

    def __init__(self) -> None:
        self.last_good: Snapshot | None = None
        self.failures: dict[str, Failure] = {}

    def apply(self, snapshot: Snapshot, failed: Mapping[str, Error]) -> Snapshot:
        """Return the snapshot to show: the new one with every failed part replaced by its last good value."""
        now = snapshot.generated_at
        carried = {source: error for source, error in failed.items() if source in CARRIED}
        previous = self.last_good
        self._track(carried, now, previous.generated_at if previous is not None else None)
        shown = snapshot
        if previous is not None and carried:
            shown = _carry(previous, snapshot, set(carried))
        self.last_good = shown
        return shown

    def tick_failed(self, error: Error, now: datetime) -> None:
        """A whole tick raised: the shown snapshot is untouched; only the indicator changes."""
        held = self.failures.get(TICK)
        good_at = self.last_good.generated_at if self.last_good is not None else None
        self.failures[TICK] = Failure(TICK, error, held.since if held else now, held.good_at if held else good_at)

    def indicator(self, now: datetime) -> str | None:
        """`! sources failed since T`, then the age of what is on screen: the oldest carried content."""
        if not self.failures:
            return None
        ordered = sorted(self.failures.values(), key=lambda failure: (failure.since, failure.source))
        names = ", ".join(failure.source for failure in ordered)
        text = f"! {names} failed since {clock(ordered[0].since, seconds=True)}"
        good = [failure.good_at for failure in ordered if failure.good_at is not None]
        if not good:
            return f"{text} · nothing read yet"
        oldest = min(good)
        return f"{text} · showing data from {clock(oldest, seconds=True)} ({_age(int((now - oldest).total_seconds()))} old)"

    def carried_since(self, error: Error) -> datetime | None:
        """When this error's source started failing, if its last good content is what is on screen."""
        return next((failure.since for failure in self.failures.values()
                     if failure.error == error and failure.good_at is not None), None)

    def phase_error(self) -> Error | None:
        """The error of the first phase-source read that is failing (the one behind `beads unavailable`)."""
        ordered = sorted((failure for failure in self.failures.values() if failure.source in PHASE),
                         key=lambda failure: (failure.since, failure.source))
        return ordered[0].error if ordered else None

    def _track(self, carried: Mapping[str, Error], now: datetime, good_at: datetime | None) -> None:
        self.failures = {
            source: Failure(source, error, self.failures[source].since, self.failures[source].good_at)
            if source in self.failures else Failure(source, error, now, good_at)
            for source, error in carried.items()
        }


def _carry(previous: Snapshot, new: Snapshot, sources: set[str]) -> Snapshot:
    # The failed reads' errors stay in the snapshot (the renderers tag them `carried since T`); generated_at is the
    # tick's own clock, and the indicator carries the age of the content.
    errors = new.errors
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
