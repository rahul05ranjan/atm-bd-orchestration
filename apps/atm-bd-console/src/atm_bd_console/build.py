"""Build identity visible to operators and bug reports."""
from __future__ import annotations

from pathlib import Path

from . import __version__, git_source
from .result import Err

try:
    from ._build_identity import IDENTITY as BUILT_IDENTITY
except ImportError:
    BUILT_IDENTITY: str | None = None


def identity(root: Path | None = None) -> str:
    """Full identity: <major>.<minor>.<commit count>+g<sha>[-dirty] (what --version prints)."""
    return _checkout_identity(root) or BUILT_IDENTITY or f"{__version__}+unknown"


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
    if isinstance(revision, Err) or isinstance(count, Err) or not count.value.stdout.strip().isdigit():
        return f"{__version__}+unknown"
    major, minor, _patch = __version__.split(".", 2)
    dirty = git_source._run(root, "status", "--porcelain")
    dirty_marker = "-dirty" if not isinstance(dirty, Err) and dirty.value.stdout.strip() else ""
    return f"{major}.{minor}.{count.value.stdout.strip()}+g{revision.value.stdout.strip()}{dirty_marker}"
