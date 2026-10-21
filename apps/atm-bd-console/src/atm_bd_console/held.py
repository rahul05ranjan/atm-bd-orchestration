"""One value that keeps its last good content: a failed read never replaces it, it only marks it stale."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Generic, TypeVar

from .error_text import error_text
from .result import Error, Ok, Result
from .timefmt import _age, clock

T = TypeVar("T")


@dataclass(frozen=True)
class Held(Generic[T]):
    """The last good value, when it was read, and the typed error of the read after it, if that failed."""

    value: T | None = None
    good_at: datetime | None = None
    error: Error | None = None

    def apply(self, result: Result[T] | None, now: datetime) -> Held[T]:
        """A good read replaces the value and clears the error; a failed read keeps the value; None is no read."""
        if result is None:
            return self
        if isinstance(result, Ok):
            return Held(result.value, now)
        return Held(self.value, self.good_at, result.error)


def held_view(held: Held[T], label: str, now: datetime) -> tuple[T | None, str]:
    """The value to render (None until one was read) and the one-line note for it; the note is empty when fresh."""
    if held.error is None:
        return held.value, "" if held.good_at is not None else f"{label}: not loaded yet"
    detail = error_text(held.error)
    if held.good_at is None:
        return None, f"{label}: not loaded - {detail}"
    age = _age(int((now - held.good_at).total_seconds()))
    return held.value, f"{label}: stale {age} (last good {clock(held.good_at, seconds=True)}) - {detail}"


