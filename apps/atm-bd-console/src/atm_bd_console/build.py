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
    root = root or Path(__file__).resolve().parents[4]
    toplevel = git_source._run(root, "rev-parse", "--show-toplevel")
    package = root / "apps" / ("atm-bd" + "-console") / "pyproject.toml"
    if isinstance(toplevel, Err) or Path(toplevel.value.stdout.strip()).resolve() != root.resolve() or not package.is_file():
        return BUILT_IDENTITY or f"{__version__}+unknown"
    revision = git_source._run(root, "rev-parse", "--short", "HEAD")
    count = git_source._run(root, "rev-list", "--count", "HEAD")
    if isinstance(revision, Err) or isinstance(count, Err) or not count.value.stdout.strip().isdigit():
        return f"{__version__}+unknown"
    major, minor, _patch = __version__.split(".", 2)
    dirty = git_source._run(root, "status", "--porcelain")
    dirty_marker = "-dirty" if not isinstance(dirty, Err) and dirty.value.stdout.strip() else ""
    return f"{major}.{minor}.{count.value.stdout.strip()}+g{revision.value.stdout.strip()}{dirty_marker}"
