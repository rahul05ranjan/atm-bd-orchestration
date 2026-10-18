from __future__ import annotations

from collections.abc import Iterable

from .enums import PrState
from .types import PullRequest


def select_pr(candidates: Iterable[PullRequest]) -> PullRequest | None:
    priority = {PrState.OPEN: 2, PrState.DRAFT: 2, PrState.MERGED: 1, PrState.CLOSED: 0}
    return max(candidates, key=lambda value: (priority.get(value.state, -1), value.number), default=None)
