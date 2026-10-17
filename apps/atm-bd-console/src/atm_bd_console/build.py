"""Build identity visible to operators and bug reports."""
from __future__ import annotations

import subprocess
from pathlib import Path

from . import __version__


def identity(root: Path | None = None) -> str:
    root = root or Path(__file__).resolve().parents[4]
    revision = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=False
    )
    if revision.returncode != 0:
        return f"{__version__}+unknown"
    count = subprocess.run(
        ["git", "-C", str(root), "rev-list", "--count", "HEAD"], capture_output=True, text=True, check=False
    )
    if count.returncode != 0 or not count.stdout.strip().isdigit():
        return f"{__version__}+unknown"
    major, minor, _patch = __version__.split(".", 2)
    dirty = subprocess.run(
        ["git", "-C", str(root), "diff", "--quiet"], capture_output=True, check=False
    )
    return f"{major}.{minor}.{count.stdout.strip()}+g{revision.stdout.strip()}{'-dirty' if dirty.returncode == 1 else ''}"
