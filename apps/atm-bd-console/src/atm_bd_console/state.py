"""Crash-safe, inspectable runtime files for one console phase."""
from __future__ import annotations

import fcntl
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self

from .codec import from_json, to_json
from .result import Err, IoFailed, LockHeld, NotFound, Ok, Result
from .types import ActionLine, MergeFile, PrsFile, Snapshot, State


class StateStore:
    """The sole writer for a phase's files while its advisory lock is held."""

    def __init__(self, directory: Path, lock_file: Any) -> None:
        self.directory = directory
        self._lock_file = lock_file

    def close(self) -> None:
        """Release promptly; process death also releases this OS lock."""
        if self._lock_file is not None:
            try:
                fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
            finally:
                self._lock_file.close()
                self._lock_file = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def write_snapshot(self, value: Snapshot) -> Result[None]:
        return self._write("snapshot.json", value)

    def write_state(self, value: State) -> Result[None]:
        return self._write("state.json", value)

    def write_merges(self, value: MergeFile) -> Result[None]:
        return self._write("merge.json", value)

    def write_prs(self, value: PrsFile) -> Result[None]:
        return self._write("prs.json", value)

    def append_action(self, value: ActionLine) -> Result[None]:
        path = self.directory / "actions.jsonl"
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(to_json(value) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            return Ok(None)
        except (OSError, ValueError) as error:
            return Err(IoFailed("io_failed", str(path), str(error), "repair the state directory permissions"))

    def _write(self, name: str, value: object) -> Result[None]:
        path = self.directory / name
        try:
            payload = to_json(value)
            descriptor, temporary = tempfile.mkstemp(prefix=f".{name}.", dir=self.directory)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            return Ok(None)
        except (OSError, ValueError) as error:
            return Err(IoFailed("io_failed", str(path), str(error), "repair the state directory permissions"))


def state_directory(repo_root: str | Path, root_id: str) -> Path:
    return Path(repo_root) / ".atm-bd" / "atm-bd-console" / root_id


def acquire_lock(repo_root: str | Path, root_id: str) -> Result[StateStore]:
    """Create state storage and acquire its non-blocking process-lifetime lock."""
    directory = state_directory(repo_root, root_id)
    lock_path = directory / "lock"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        ignore = directory.parent / ".gitignore"
        if not ignore.exists():
            ignore.write_text("*\n", encoding="utf-8")
        handle = lock_path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return Err(LockHeld("lock_held", _snapshot_age(directory), "wait for the running console process"))
        return Ok(StateStore(directory, handle))
    except OSError as error:
        return Err(IoFailed("io_failed", str(lock_path), str(error), "repair the state directory permissions"))


def load_snapshot(repo_root: str | Path, root_id: str) -> Result[Snapshot]:
    return _load(repo_root, root_id, "snapshot.json", Snapshot)


def load_state(repo_root: str | Path, root_id: str) -> Result[State]:
    return _load(repo_root, root_id, "state.json", State)


def load_merges(repo_root: str | Path, root_id: str) -> Result[MergeFile]:
    return _load(repo_root, root_id, "merge.json", MergeFile)


def load_prs(repo_root: str | Path, root_id: str) -> Result[PrsFile]:
    return _load(repo_root, root_id, "prs.json", PrsFile)


def _load(repo_root: str | Path, root_id: str, name: str, cls: type[Any]) -> Result[Any]:
    path = state_directory(repo_root, root_id) / name
    if not path.is_file():
        return Err(NotFound("not_found", str(path), "run a console tick to create it"))
    try:
        decoded = from_json(cls, path.read_text(encoding="utf-8"))
        return decoded
    except OSError as error:
        return Err(IoFailed("io_failed", str(path), str(error), "repair the state directory permissions"))


def _snapshot_age(directory: Path) -> int | None:
    path = directory / "snapshot.json"
    if not path.is_file():
        return None
    decoded = from_json(Snapshot, path.read_text(encoding="utf-8"))
    if isinstance(decoded, Err):
        return None
    return max(0, int((datetime.now(UTC) - decoded.value.generated_at).total_seconds()))
