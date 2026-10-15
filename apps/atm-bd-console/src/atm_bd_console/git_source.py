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
            # A bare repository's record has no HEAD line: it has no working tree, so it is not a worktree row.
            if "head" in current:
                rows.append(Worktree(current["path"], current.get("branch"), current["head"]))
                current = {}
        elif line.startswith("worktree "):
            current["path"] = line.removeprefix("worktree ")
        elif line.startswith("HEAD "):
            current["head"] = line.removeprefix("HEAD ")
        elif line.startswith("branch refs/heads/"):
            current["branch"] = line.removeprefix("branch refs/heads/")
    return Ok(tuple(rows))


def _conflict_output(stdout: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split merge-tree -z --name-only output: tree oid NUL, files NUL... NUL NUL, then one record per message:
    <path count> NUL <path>... NUL <conflict type> NUL <message> NUL."""
    head, _, informational = stdout.partition("\0\0")
    files = tuple(head.split("\0")[1:])
    tokens = informational.split("\0")
    messages: list[str] = []
    index = 0
    while index < len(tokens) and tokens[index]:
        index += 1 + int(tokens[index]) + 1  # path count, the paths, the conflict type
        messages.append(tokens[index].rstrip("\n"))
        index += 1
    return files, tuple(messages)


def merge(repo: str | Path, branch: str, target: str) -> Result[Merge]:
    for ref in (f"origin/{target}", f"origin/{branch}"):
        verify = ("git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        found = _run(repo, *verify[1:])
        if found.returncode == 1:
            return Err(NotFound("not_found", ref, "fetch the named branch and retry"))
        if found.returncode:
            return _failed(verify, found)
    command = ("git", "merge-tree", "--write-tree", "--name-only", "-z", f"origin/{target}", f"origin/{branch}")
    result = _run(repo, *command[1:])
    if result.returncode not in (0, 1):
        return _failed(command, result)
    checked_at = datetime.now(UTC)
    if result.returncode == 0:
        return Ok(Merge(branch, target, "clean", (), (), checked_at))
    files, messages = _conflict_output(result.stdout)
    return Ok(Merge(branch, target, "conflict", files, messages, checked_at))
