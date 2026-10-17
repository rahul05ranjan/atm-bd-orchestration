"""Build identity visible to operators and bug reports."""
from __future__ import annotations

from pathlib import Path

from . import __version__
from . import git_source
from .result import Err


def identity(root: Path | None = None) -> str:
    root = root or Path(__file__).resolve().parents[4]
    revision = git_source._run(root, "rev-parse", "--short", "HEAD")
    count = git_source._run(root, "rev-list", "--count", "HEAD")
    if isinstance(revision, Err) or isinstance(count, Err) or not count.value.stdout.strip().isdigit():
        return f"{__version__}+unknown"
    major, minor, _patch = __version__.split(".", 2)
    dirty = git_source._run(root, "diff", "--quiet", ok_codes=(0, 1))
    dirty_marker = "-dirty" if not isinstance(dirty, Err) and dirty.value.code == 1 else ""
    return f"{major}.{minor}.{count.value.stdout.strip()}+g{revision.value.stdout.strip()}{dirty_marker}"
