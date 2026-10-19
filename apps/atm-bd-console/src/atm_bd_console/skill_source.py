"""Resolve bundled and released atm-bd-orchestration installer data."""
from __future__ import annotations

import io
import json
import re
import tarfile
import tempfile
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from . import __version__, command, skill_integrity
from .result import Err, Timeout

REPOSITORY = "https://github.com/randlee/atm-bd-orchestration"
# The published release assets: the sdist carries the skill manifest the release's tag is verified against.
RELEASES = "https://github.com/randlee/atm-bd-orchestration/releases/download"
LATEST_RELEASE_API = "https://api.github.com/repos/randlee/atm-bd-orchestration/releases/latest"
HTTP_TIMEOUT_S = 60
_PLUGIN = Path("plugins/atm-bd-orchestration")
_REQUIRED_FILES = ("install.py", "manifest.yaml", "registry.yaml")
_TAG_RE = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+\Z")
INSTALL_ERROR_TYPES = (ImportError, KeyError, OSError, RuntimeError, TypeError, ValueError)
# A shallow clone over a slow link needs far longer than the 20 s default for quick queries.
CLONE_TIMEOUT_S = 300


class SkillSourceError(RuntimeError):
    """A source could not be listed or resolved into an installable tree."""


class RepositoryError(SkillSourceError):
    """git could not read REPOSITORY; the ref is not at fault, so the caller does not relabel it."""


def install_failure_message(name: str, error: Exception) -> str:
    """Format an installer-tree failure once for both bundled and released sources."""
    if name == "bundled":
        return str(error)
    return f"{name} is not installable by atm-bd {__version__}: {error}"


def build_commit() -> str:
    """Return the build identity commit only when it is a full lowercase git SHA."""
    return skill_integrity.read_build_commit(Path(__file__).with_name("_build_identity.py")) or "unknown"


@dataclass(frozen=True)
class SkillSource:
    name: str
    pkg_dir: Path
    commit: str
    # The published skill-manifest.json of the release this tree is; None when none was published.
    manifest: bytes | None = None


def bundled_directory() -> Path:
    """Return the skills packaged into atm_bd_console/_skills at build time; nothing else is consulted."""
    packaged = Path(__file__).resolve().parent / "_skills"
    if not packaged.is_dir():
        raise SkillSourceError("bundled skills are missing from this atm-bd install; reinstall atm-bd")
    return packaged


def bundled_manifest() -> bytes | None:
    """The skill-manifest.json packaged beside the bundled skills at build time; read from disk, never the network."""
    path = Path(__file__).resolve().parent / skill_integrity.MANIFEST_NAME
    return path.read_bytes() if path.is_file() else None


def _http_get(url: str) -> bytes | None:
    """The response body, or None when the URL does not exist (HTTP 404)."""
    request = urllib.request.Request(url, headers={"Accept": "application/octet-stream, application/json"})
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise RepositoryError(f"{url}: HTTP {error.code} {error.reason}") from error
    except (urllib.error.URLError, OSError) as error:
        raise RepositoryError(f"{url}: {getattr(error, 'reason', error)}") from error


def release_manifest(tag: str) -> bytes | None:
    """skill-manifest.json from the `atm_bd-<version>.tar.gz` asset of release `tag`; None when any part is absent."""
    version = tag.removeprefix("v")
    archive = _http_get(f"{RELEASES}/{tag}/atm_bd-{version}.tar.gz")
    if archive is None:
        return None
    member = f"atm_bd-{version}/src/atm_bd_console/{skill_integrity.MANIFEST_NAME}"
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as sdist:
            entry = sdist.extractfile(member)
            if entry is None:
                return None
            return entry.read()
    except KeyError:
        return None
    except (tarfile.TarError, OSError, EOFError) as error:
        raise SkillSourceError(f"{tag}: atm_bd-{version}.tar.gz is not a readable sdist: {error}") from error


def latest_release_tag() -> str | None:
    """The tag of the latest published release, or None when the repository has published none."""
    body = _http_get(LATEST_RELEASE_API)
    if body is None:
        return None
    try:
        tag = json.loads(body).get("tag_name")
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError) as error:
        raise RepositoryError(f"{LATEST_RELEASE_API}: not a release object: {error}") from error
    if not isinstance(tag, str) or not _TAG_RE.fullmatch(tag):
        raise RepositoryError(f"{LATEST_RELEASE_API}: latest release tag {tag!r} is not vX.Y.Z")
    return tag


def _run_git(args: list[str], *, timeout_s: int = command.DEFAULT_TIMEOUT_S) -> str:
    result = command.run(["git", *args], f"check access to {REPOSITORY} and retry", timeout_s=timeout_s)
    if isinstance(result, Err):
        error = result.error
        detail = getattr(error, "stderr", "") or (
            f"timed out after {error.seconds}s" if isinstance(error, Timeout) else error.kind
        )
        raise RepositoryError(f"{REPOSITORY}: git {' '.join(args)} failed: {detail}")
    return result.value.stdout


def remote_tags() -> tuple[str, ...]:
    """List the release tags upstream, sorted like `git tag -l`."""
    output = _run_git(["ls-remote", "--tags", "--refs", REPOSITORY])
    tags = {
        ref.removeprefix("refs/tags/")
        for line in output.splitlines()
        if len(parts := line.split()) == 2
        for ref in (parts[1],)
        if ref.startswith("refs/tags/")
    }
    return tuple(sorted(tags))


def _validate_pkg_dir(pkg_dir: Path, source: str) -> None:
    missing = [name for name in _REQUIRED_FILES if not (pkg_dir / name).is_file()]
    if missing:
        raise SkillSourceError(f"{source} is not installable: missing {', '.join(missing)}")


@contextmanager
def resolve_source(*, version: str | None = None, latest: bool = False) -> Iterator[SkillSource]:
    """Yield installer data and its published manifest: the packaged tree, or a shallow clone of a release tag
    (`--latest` is the latest published release); no fetched Python is imported or executed."""
    if version is not None and latest:
        raise SkillSourceError("choose a version or --latest, not both")
    if version is None and not latest:
        pkg_dir = bundled_directory()
        _validate_pkg_dir(pkg_dir, "bundled")
        commit = build_commit()
        manifest = bundled_manifest()
        if manifest is None:
            raise SkillSourceError(skill_integrity.no_manifest_message("bundled"))
        yield SkillSource("bundled", pkg_dir, commit, manifest)
        return

    tags = remote_tags()
    if latest:
        latest_tag = latest_release_tag()
        if latest_tag is None:
            raise SkillSourceError(skill_integrity.no_manifest_message("latest release"))
        if latest_tag not in tags:
            raise RepositoryError(f"{REPOSITORY}: latest release {latest_tag} has no tag")
        ref = latest_tag
    else:
        assert version is not None
        ref = version if version.startswith("v") else f"v{version}"
        if not _TAG_RE.fullmatch(ref) or ref not in tags:
            offered = ", ".join(tags) if tags else "(none)"
            raise SkillSourceError(f"unknown version {version}; available tags: {offered}")
    name = ref
    manifest = release_manifest(ref)
    if manifest is None:
        raise SkillSourceError(skill_integrity.no_manifest_message(ref))

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
            if not skill_integrity.valid_build_commit(commit):
                raise RepositoryError(f"{REPOSITORY}: git rev-parse HEAD returned an invalid commit: {commit!r}")
        except RepositoryError:
            raise
        except (SkillSourceError, OSError) as error:
            raise SkillSourceError(install_failure_message(ref, error)) from error
        # Outside the translation: errors raised by the caller's work with the tree are not the ref's.
        yield SkillSource(name, pkg_dir, commit, manifest)
