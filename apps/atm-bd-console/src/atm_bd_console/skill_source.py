"""Resolve bundled and released atm-bd-orchestration installer data."""
from __future__ import annotations

import re
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path

from . import __version__, command
from .result import Err, Timeout

REPOSITORY = "https://github.com/randlee/atm-bd-orchestration"
_PLUGIN = Path("plugins/atm-bd-orchestration")
_REQUIRED_FILES = ("install.py", "manifest.yaml", "registry.yaml")
_TAG_RE = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+\Z")
_COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
# A shallow clone over a slow link needs far longer than the 20 s default for quick queries.
CLONE_TIMEOUT_S = 300


class SkillSourceError(RuntimeError):
    """A source could not be listed or resolved into an installable tree."""


@dataclass(frozen=True)
class SkillSource:
    name: str
    pkg_dir: Path
    commit: str


def bundled_directory() -> Path:
    """Return the packaged bundle, or the plugin in an editable source checkout."""
    packaged = Path(__file__).resolve().parent / "_skills"
    checkout = Path(__file__).resolve().parents[4] / _PLUGIN
    if packaged.is_dir():
        return packaged
    if checkout.is_dir():
        return checkout
    raise SkillSourceError(f"bundled {REPOSITORY}/plugins/atm-bd-orchestration is missing")


def _run_git(args: list[str], *, timeout_s: int = command.DEFAULT_TIMEOUT_S) -> str:
    result = command.run(["git", *args], f"check access to {REPOSITORY} and retry", timeout_s=timeout_s)
    if isinstance(result, Err):
        error = result.error
        detail = getattr(error, "stderr", "") or (
            f"timed out after {error.seconds}s" if isinstance(error, Timeout) else error.kind
        )
        raise SkillSourceError(f"{REPOSITORY}: git {' '.join(args)} failed: {detail}")
    return result.value.stdout


def _remote_tags() -> tuple[str, ...]:
    output = _run_git(["ls-remote", "--tags", "--refs", REPOSITORY])
    tags = {
        ref.removeprefix("refs/tags/")
        for line in output.splitlines()
        if len(parts := line.split()) == 2
        for ref in (parts[1],)
        if ref.startswith("refs/tags/")
    }
    return tuple(sorted(tags))


def list_tags() -> tuple[str, ...]:
    """List release refs from the upstream repository, in `git tag -l` order."""
    return _remote_tags()


def _validate_pkg_dir(pkg_dir: Path, source: str) -> None:
    missing = [name for name in _REQUIRED_FILES if not (pkg_dir / name).is_file()]
    if missing:
        raise SkillSourceError(f"{source} is not installable: missing {', '.join(missing)}")


@contextmanager
def resolve_source(*, version: str | None = None, latest: bool = False) -> Iterator[SkillSource]:
    """Yield installer data from a shallow clone; no fetched Python is imported or executed."""
    if version is not None and latest:
        raise SkillSourceError("choose a version or --latest, not both")
    if version is None and not latest:
        pkg_dir = bundled_directory()
        _validate_pkg_dir(pkg_dir, "bundled")
        try:
            COMMIT = import_module("._build_identity", __package__).COMMIT
        except ImportError:
            COMMIT = None
        commit = COMMIT if isinstance(COMMIT, str) and _COMMIT_RE.fullmatch(COMMIT) else "unknown"
        yield SkillSource("bundled", pkg_dir, commit)
        return

    tags = _remote_tags()
    if latest:
        ref, name = "main", "main"
    else:
        assert version is not None
        ref = version if version.startswith("v") else f"v{version}"
        if not _TAG_RE.fullmatch(ref) or ref not in tags:
            offered = ", ".join(tags) if tags else "(none)"
            raise SkillSourceError(f"unknown version {version}; available tags: {offered}")
        name = ref

    with tempfile.TemporaryDirectory(prefix="atm-bd-skill-") as temporary:
        try:
            checkout = Path(temporary) / "checkout"
            _run_git(
                ["clone", "--depth", "1", "--single-branch", "--branch", ref, REPOSITORY, str(checkout)],
                timeout_s=CLONE_TIMEOUT_S,
            )
            pkg_dir = checkout / _PLUGIN
            _validate_pkg_dir(pkg_dir, ref)
            commit = _run_git(["-C", str(checkout), "rev-parse", "HEAD"]).strip()
            if not _COMMIT_RE.fullmatch(commit):
                raise SkillSourceError(f"{REPOSITORY}: git rev-parse HEAD returned an invalid commit: {commit!r}")
        except SkillSourceError as error:
            if error.args and str(error).startswith(f"{REPOSITORY}:"):
                raise
            raise SkillSourceError(f"{ref} is not installable by atm-bd {__version__}: {error}") from error
        except OSError as error:
            raise SkillSourceError(f"{ref} is not installable by atm-bd {__version__}: {error}") from error
        # Outside the translation: errors raised by the caller's work with the tree are not the ref's.
        yield SkillSource(name, pkg_dir, commit)
