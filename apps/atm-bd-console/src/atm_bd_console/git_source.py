"""Git reads for the console; every command result is converted at this boundary."""
from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

from .result import CommandFailed, Err, NotFound, Ok, Result
from .types import Merge, RemoteRef, Worktree


def _run(repo: str | Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(("git", *args), cwd=repo, capture_output=True, text=True, check=False)


def _failed(command: tuple[str, ...], result: subprocess.CompletedProcess[str]) -> Err:
    return Err(CommandFailed("command_failed", command, result.returncode, result.stderr.strip(), "check the git repository and retry"))


def fetch(repo: str | Path) -> Result[None]:
    command = ("git", "fetch", "origin", "--prune")
    result = _run(repo, *command[1:])
    return Ok(None) if result.returncode == 0 else _failed(command, result)


def remote_refs(repo: str | Path) -> Result[tuple[RemoteRef, ...]]:
    command = ("git", "for-each-ref", "--format=%(refname:lstrip=3) %(objectname)", "refs/remotes/origin")
    result = _run(repo, *command[1:])
    if result.returncode:
        return _failed(command, result)
    rows = []
    for line in result.stdout.splitlines():
        branch, sha = line.split(maxsplit=1)
        if branch != "HEAD":
            rows.append(RemoteRef(branch, sha))
    return Ok(tuple(rows))


def worktrees(repo: str | Path) -> Result[tuple[Worktree, ...]]:
    command = ("git", "worktree", "list", "--porcelain")
    result = _run(repo, *command[1:])
    if result.returncode:
        return _failed(command, result)
    rows, current = [], {}
    for line in [*result.stdout.splitlines(), ""]:
        if not line:
            if current:
                rows.append(Worktree(current["path"], current.get("branch"), current["head"]))
                current = {}
        elif line.startswith("worktree "):
            current["path"] = line.removeprefix("worktree ")
        elif line.startswith("HEAD "):
            current["head"] = line.removeprefix("HEAD ")
        elif line.startswith("branch refs/heads/"):
            current["branch"] = line.removeprefix("branch refs/heads/")
    return Ok(tuple(rows))


def merge(repo: str | Path, branch: str, target: str) -> Result[Merge]:
    command = ("git", "merge-tree", "--write-tree", "--name-only", "-z", f"origin/{target}", f"origin/{branch}")
    result = _run(repo, *command[1:])
    if result.returncode not in (0, 1):
        return _failed(command, result)
    tokens = tuple(token for token in result.stdout.split("\0") if token and len(token) != 40)
    checked_at = datetime.now(UTC)
    if result.returncode == 0:
        return Ok(Merge(branch, target, "clean", (), (), checked_at))
    if not tokens and "unknown revision" in result.stderr:
        return Err(NotFound("branch", "fetch the named branch and retry"))
    return Ok(Merge(branch, target, "conflict", tokens, tuple(line for line in result.stderr.splitlines() if line), checked_at))
