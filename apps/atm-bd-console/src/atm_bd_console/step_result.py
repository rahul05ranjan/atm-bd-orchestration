"""Neutral contract type for the runner's tick result."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from .result import Error, Result
    from .types import Action, Snapshot, State


class StepResult(tuple["Snapshot", "State"]):
    """Two-value compatible tick result carrying actions and the run tabs' reads (None: not read this tick)."""

    reported: tuple[Action, ...]
    sanity: Result[list] | None
    quality: Result[tuple[list, list[dict]]] | None
    failed_reads: Mapping[str, Error]
    prs_read_at: datetime | None  # when the PR rows were last read good; None: never

    def __new__(
        cls,
        snapshot: Snapshot,
        state: State,
        reported: tuple[Action, ...] = (),
        sanity: Result[list] | None = None,
        quality: Result[tuple[list, list[dict]]] | None = None,
        failed_reads: Mapping[str, Error] | None = None,
        prs_read_at: datetime | None = None,
    ) -> Self:
        result = super().__new__(cls, (snapshot, state))
        result.reported = reported
        result.sanity = sanity
        result.quality = quality
        result.failed_reads = dict(failed_reads or {})
        result.prs_read_at = prs_read_at
        return result
