"""Neutral contract type for the runner's tick result."""
from __future__ import annotations

from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from .types import Action, Snapshot, State


class StepResult(tuple["Snapshot", "State"]):
    """Two-value compatible tick result carrying actions and run-tab state."""

    reported: tuple[Action, ...]
    run_data: tuple[list, list, list[dict]] | None
    runs_error: str | None

    def __new__(
        cls,
        snapshot: Snapshot,
        state: State,
        reported: tuple[Action, ...] = (),
        run_data: tuple[list, list, list[dict]] | None = None,
        runs_error: str | None = None,
    ) -> Self:
        result = super().__new__(cls, (snapshot, state))
        result.reported = reported
        result.run_data = run_data
        result.runs_error = runs_error
        return result
