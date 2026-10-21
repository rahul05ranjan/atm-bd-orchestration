"""The one text for an Error, shared by the dashboard, the dispatch reasons and the console log."""
from __future__ import annotations

from .result import (
    CommandFailed,
    ConfigInvalid,
    Error,
    IoFailed,
    LockHeld,
    NotFound,
    ParseFailed,
    Rejected,
    Timeout,
    Unreachable,
)


def error_text(error: Error) -> str:
    """The one text for an Error: its kind, what failed (command, deadline, exit code, stderr) and the fix."""
    if isinstance(error, CommandFailed):
        detail = f"{' '.join(error.argv)} (exit {error.code}): {error.stderr.strip()}"
    elif isinstance(error, Timeout):
        detail = f"{' '.join(error.argv)} timed out after {error.seconds}s"
    elif isinstance(error, Rejected):
        detail = f"{' '.join(error.argv)} (status {error.status}): {error.message}"
    elif isinstance(error, Unreachable):
        detail = f"{' '.join(error.argv)}: {error.message}"
    elif isinstance(error, ParseFailed):
        detail = f"{error.source}: {error.message}"
    elif isinstance(error, NotFound):
        detail = error.what
    elif isinstance(error, ConfigInvalid):
        detail = f"{error.key}={error.value}"
    elif isinstance(error, LockHeld):
        age = "unknown" if error.snapshot_age_s is None else f"{error.snapshot_age_s}s"
        detail = f"snapshot age {age}"
    elif isinstance(error, IoFailed):
        detail = f"{error.path}: {error.message}"
    else:
        # A new Error variant must get its own branch here; never fall through to an unset detail.
        raise TypeError(f"unknown error variant {type(error).__name__}; add it to error_text")
    return f"{error.kind}: {detail}; {error.fix}"
