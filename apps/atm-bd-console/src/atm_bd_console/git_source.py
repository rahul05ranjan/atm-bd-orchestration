"""Git reads for the console; every command result is converted at this boundary."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from . import command
from .enums import MergeState
from .result import CommandFailed, Err, NotFound, Ok, Result, Timeout
from .types import Merge, RemoteRef, Worktree

# git fetch talks to the remote; local reads get the default timeout.
FETCH_TIMEOUT_S = 60
FIX = "check the git repository and retry"


def _run(repo: str | Path, *args: str, timeout_s: int = command.DEFAULT_TIMEOUT_S,
         ok_codes: tuple[int, ...] = (0,)) -> Result[command.Output]:
    argv = ("git", *args)
    result = command.run(argv, FIX, cwd=repo, timeout_s=timeout_s, ok_codes=ok_codes)
    if isinstance(result, Err) and isinstance(result.error, Timeout):
        fix = (f"git or the remote is slow or the host is loaded; the call waited {result.error.seconds}s; "
               f"run `git -C {repo} {' '.join(args)}` by hand to see how long it takes")
        return Err(replace(result.error, fix=fix))
    if isinstance(result, Err) and isinstance(result.error, CommandFailed):
        fix = f"`git {' '.join(args)}` exited {result.error.code}; repair what its stderr reports in {repo}, then rerun it by hand"
        return Err(replace(result.error, fix=fix))
    return result


def fetch(repo: str | Path) -> Result[None]:
    result = _run(repo, "fetch", "origin", "--prune", timeout_s=FETCH_TIMEOUT_S)
    return result if isinstance(result, Err) else Ok(None)


def remote_refs(repo: str | Path) -> Result[tuple[RemoteRef, ...]]:
    result = _run(repo, "for-each-ref", "--format=%(refname:lstrip=3) %(objectname)", "refs/remotes/origin")
    if isinstance(result, Err):
        return result
    rows = []
    for line in result.value.stdout.splitlines():
        branch, sha = line.split(maxsplit=1)
        if branch != "HEAD":
            rows.append(RemoteRef(branch, sha))
    return Ok(tuple(rows))


def worktrees(repo: str | Path) -> Result[tuple[Worktree, ...]]:
    result = _run(repo, "worktree", "list", "--porcelain")
    if isinstance(result, Err):
        return result
    rows, current = [], {}
    for line in [*result.value.stdout.splitlines(), ""]:
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
        found = _run(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", ok_codes=(0, 1))
        if isinstance(found, Err):
            return found
        if found.value.code == 1:
            return Err(NotFound("not_found", ref, "fetch the named branch and retry"))
    result = _run(repo, "merge-tree", "--write-tree", "--name-only", "-z", f"origin/{target}", f"origin/{branch}",
                  ok_codes=(0, 1))
    if isinstance(result, Err):
        return result
    checked_at = datetime.now(UTC)
    if result.value.code == 0:
        return Ok(Merge(branch, target, MergeState.CLEAN, (), (), checked_at))
    files, messages = _conflict_output(result.value.stdout)
    return Ok(Merge(branch, target, MergeState.CONFLICT, files, messages, checked_at))
