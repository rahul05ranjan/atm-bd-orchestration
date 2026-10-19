"""The published skill manifest: build it, read it, verify a skill tree against it, record an install.

The build hook loads this file by path, so it imports only the standard library and `sc_sha`.
The manifest detects skill files that differ from what was published; it is not a signature.
"""
from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

import sc_sha

MANIFEST_NAME = "skill-manifest.json"
RECORD_NAME = "atm-bd-install.json"
SCHEMA = 1
_COMPOSITION_SCHEMA = "sc-sha/manifest/v1"
_COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")

Status = Literal["ok", "modified", "missing", "extra"]


class IntegrityError(RuntimeError):
    """A published manifest is absent or unreadable, so the tree cannot be verified."""


class ManifestSource(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def manifest(self) -> bytes | None: ...

    @property
    def parsed_manifest(self) -> dict[str, Any] | None: ...

    @property
    def expected_version(self) -> str | None: ...


@dataclass(frozen=True)
class PathResult:
    path: str
    status: Status


def no_manifest_message(ref: str) -> str:
    return f"{ref} has no published {MANIFEST_NAME}; cannot verify"


def valid_build_commit(value: object) -> bool:
    """Whether a build identity contains a full, lowercase git commit hash."""
    return isinstance(value, str) and _COMMIT_RE.fullmatch(value) is not None


def read_build_commit(identity_file: Path) -> str | None:
    """Read and validate the generated COMMIT assignment from a build identity module."""
    try:
        lines = identity_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        key, separator, raw = line.partition("=")
        if separator and key.strip() == "COMMIT":
            try:
                value = ast.literal_eval(raw.strip())
            except (SyntaxError, ValueError):
                return None
            return value if valid_build_commit(value) else None
    return None


def shipped_files(root: Path) -> list[str]:
    """Every file under root in sorted relative-path order, without byte-compiled Python."""
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.relative_to(root).parts and path.suffix != ".pyc"
    )


def file_sha(data: bytes) -> str:
    return sc_sha.calculate_hash({"utf8_file_bytes": data})["sha256"]


def aggregate(files: list[dict[str, str]]) -> str:
    nodes = [{"source": {"kind": "local_path", "value": entry["path"]}, "sha256": entry["sc_sha"]} for entry in files]
    return sc_sha.calculate_composition_hash({"schema": _COMPOSITION_SCHEMA, "nodes": nodes, "edges": []})["sha256"]


def build_manifest(root: Path, version: str) -> dict[str, Any]:
    files = [{"path": rel, "sc_sha": file_sha((root / rel).read_bytes())} for rel in shipped_files(root)]
    return {"schema": SCHEMA, "version": version, "files": files, "aggregate": aggregate(files)}


def _validate_manifest_version(manifest: object, ref: str, expected_version: str) -> None:
    version = manifest.get("version") if isinstance(manifest, dict) else None
    if version != expected_version:
        raise IntegrityError(
            f"{ref}: {MANIFEST_NAME} version {version!r} does not match release version {expected_version!r}"
        )


def parse_manifest(data: bytes, ref: str, *, expected_version: str | None = None) -> dict[str, Any]:
    try:
        manifest = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise IntegrityError(f"{ref}: {MANIFEST_NAME} is not JSON: {error}") from error
    if expected_version is not None:
        _validate_manifest_version(manifest, ref, expected_version)
    files = manifest.get("files") if isinstance(manifest, dict) else None
    if (
        not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA or not isinstance(manifest.get("version"), str)
        or not isinstance(files, list)
        or not all(isinstance(e, dict) and isinstance(e.get("path"), str) and isinstance(e.get("sc_sha"), str) for e in files)
    ):
        raise IntegrityError(f"{ref}: {MANIFEST_NAME} is not a schema {SCHEMA} manifest")
    paths = [entry["path"] for entry in files]
    if paths != sorted(set(paths)):
        raise IntegrityError(f"{ref}: {MANIFEST_NAME} paths are not unique and sorted")
    if manifest.get("aggregate") != aggregate(files):
        raise IntegrityError(f"{ref}: {MANIFEST_NAME} aggregate does not match its files")
    return manifest


def load_manifest(source: ManifestSource) -> dict[str, Any]:
    """The published manifest of the release the source resolved; absent means the install cannot be verified."""
    if source.parsed_manifest is not None:
        return source.parsed_manifest
    if source.manifest is None:
        raise IntegrityError(no_manifest_message(source.name))
    return parse_manifest(source.manifest, source.name, expected_version=source.expected_version)


def verify_tree(pkg_dir: Path, manifest: dict[str, Any]) -> list[PathResult]:
    """One result per path in the manifest or the tree, sorted by path."""
    published = {entry["path"]: entry["sc_sha"] for entry in manifest["files"]}
    present = set(shipped_files(pkg_dir))
    results = []
    for path in sorted(published.keys() | present):
        if path not in present:
            status: Status = "missing"
        elif path not in published:
            status = "extra"
        else:
            status = "ok" if file_sha((pkg_dir / path).read_bytes()) == published[path] else "modified"
        results.append(PathResult(path, status))
    return results


def write_install_record(
    dest: Path, version: str, commit: str, manifest: dict[str, Any], installed: dict[str, bytes], *, source: str,
) -> Path:
    """Record the installed bytes' sc-sha per repository-relative path; rendered values make them differ from published."""
    record = {
        "version": version,
        "commit": commit,
        "source": {"ref": source, "manifest_aggregate": manifest["aggregate"]},
        "files": [{"path": path, "sc_sha": file_sha(installed[path])} for path in sorted(installed)],
    }
    path = dest / RECORD_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return path
