"""Build identity visible to operators and bug reports."""
from __future__ import annotations

from dataclasses import asdict
from importlib import import_module
from pathlib import Path

from . import __version__, git_source
from .log import create_console_logger, error_message
from .result import Err, Error

try:
    BUILT_IDENTITY = import_module("._build_identity", __package__).IDENTITY
except ImportError:
    BUILT_IDENTITY: str | None = None


def identity() -> str:
    """The package version exposed by ``atm-bd --version``.

    Build provenance remains available through ``display_identity`` and the
    generated build identity module; it is not part of the release version.
    """
    return __version__


def display_identity(root: Path | None = None) -> str:
    """The build line in the TUI (#254): a release install shows only <major>.<minor>.<commit count>.

    A checkout (identity derived from git at runtime) and a build from a dirty tree keep the full form, so
    the sha and the dirty marker stay visible exactly where a debug build is running.
    """
    checkout = _checkout_identity(root)
    if checkout is not None:
        return checkout
    if BUILT_IDENTITY is not None and not BUILT_IDENTITY.endswith("-dirty"):
        return BUILT_IDENTITY.partition("+")[0]
    return BUILT_IDENTITY or f"{__version__}+unknown"


def _checkout_identity(root: Path | None) -> str | None:
    """Git-derived identity when this package runs from its own checkout, else None."""
    root = root or Path(__file__).resolve().parents[4]
    toplevel = git_source._run(root, "rev-parse", "--show-toplevel")
    package = root / "apps" / ("atm-bd" + "-console") / "pyproject.toml"
    if isinstance(toplevel, Err) or Path(toplevel.value.stdout.strip()).resolve() != root.resolve() or not package.is_file():
        return None
    revision = git_source._run(root, "rev-parse", "--short", "HEAD")
    count = git_source._run(root, "rev-list", "--count", "HEAD")
    if isinstance(revision, Err):
        _log_failed_read(root, "rev-parse", revision.error)
        return f"{__version__}+unknown"
    if isinstance(count, Err):
        _log_failed_read(root, "rev-list", count.error)
        return f"{__version__}+unknown"
    if not count.value.stdout.strip().isdigit():
        return f"{__version__}+unknown"
    major, minor, _patch = __version__.split(".", 2)
    dirty = git_source._run(root, "status", "--porcelain")
    if isinstance(dirty, Err):
        # A failed status read is not a clean tree: show it as unknown and log why.
        _log_failed_read(root, "status", dirty.error)
        dirty_marker = "-dirty?"
    else:
        dirty_marker = "-dirty" if dirty.value.stdout.strip() else ""
    return f"{major}.{minor}.{count.value.stdout.strip()}+g{revision.value.stdout.strip()}{dirty_marker}"


def _log_failed_read(root: Path, source: str, error: Error) -> None:
    """Send the original git error (argv, code, stderr) to the console log; the display string carries only the marker."""
    logger = create_console_logger(root)
    if isinstance(logger, Err):
        return
    logger.value.event(
        "error", level="error", message=f"build identity git {source} failed: {error_message(asdict(error))}",
        source=f"build_identity.{source}", error=asdict(error),
    )
