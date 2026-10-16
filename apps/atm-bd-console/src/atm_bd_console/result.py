from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Literal, TypeAlias, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class Ok(Generic[T]):
    value: T


@dataclass(frozen=True)
class CommandFailed:
    kind: Literal["command_failed"]
    argv: tuple[str, ...]
    code: int
    stderr: str
    fix: str


@dataclass(frozen=True)
class Timeout:
    kind: Literal["timeout"]
    argv: tuple[str, ...]
    seconds: int
    fix: str


@dataclass(frozen=True)
class Rejected:
    kind: Literal["rejected"]
    argv: tuple[str, ...]
    status: int
    message: str
    fix: str


@dataclass(frozen=True)
class Unreachable:
    kind: Literal["unreachable"]
    argv: tuple[str, ...]
    message: str
    fix: str


@dataclass(frozen=True)
class ParseFailed:
    kind: Literal["parse_failed"]
    source: str
    message: str
    fix: str


@dataclass(frozen=True)
class NotFound:
    kind: Literal["not_found"]
    what: str
    fix: str


@dataclass(frozen=True)
class ConfigInvalid:
    kind: Literal["config_invalid"]
    key: str
    value: str
    fix: str


@dataclass(frozen=True)
class LockHeld:
    kind: Literal["lock_held"]
    snapshot_age_s: int | None
    fix: str


@dataclass(frozen=True)
class IoFailed:
    kind: Literal["io_failed"]
    path: str
    message: str
    fix: str


Error: TypeAlias = CommandFailed | Timeout | Rejected | Unreachable | ParseFailed | NotFound | ConfigInvalid | LockHeld | IoFailed


@dataclass(frozen=True)
class Err:
    error: Error


Result: TypeAlias = Ok[T] | Err
