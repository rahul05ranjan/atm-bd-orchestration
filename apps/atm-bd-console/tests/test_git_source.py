"""git_source against a real temporary repository with a bare origin (real git, no fake)."""
from __future__ import annotations

from pathlib import Path
from typing import TypeVar

import pytest
from process_helpers import run_process

from atm_bd_console import git_source
from atm_bd_console.enums import MergeState
from atm_bd_console.result import Err, NotFound, Ok, Result

T = TypeVar("T")


def ok(value: Result[T]) -> T:
    assert isinstance(value, Ok)
    return value.value


def git(cwd: Path, *args: str) -> str:
    return run_process("git source fixture", ("git", *args), cwd=cwd).stdout.strip()


def commit(repo: Path, message: str, **files: str) -> str:
    for name, text in files.items():
        (repo / name).write_text(text)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A clone of a bare origin holding main, a clean branch and a branch that conflicts with main in two files."""
    for name, value in (("GIT_CONFIG_GLOBAL", "/dev/null"), ("GIT_CONFIG_NOSYSTEM", "1"),
                        ("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@t"),
                        ("GIT_COMMITTER_NAME", "t"), ("GIT_COMMITTER_EMAIL", "t@t")):
        monkeypatch.setenv(name, value)
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    work = tmp_path / "work"
    git(tmp_path, "clone", "-q", str(origin), str(work))
    git(work, "checkout", "-q", "-b", "main")
    commit(work, "base", **{"a.txt": "a\n", "b.txt": "b\n", "c.txt": "c\n"})
    git(work, "push", "-q", "origin", "main")
    git(work, "checkout", "-q", "-b", "clean")
    commit(work, "clean", **{"c.txt": "clean\n"})
    git(work, "push", "-q", "origin", "clean")
    git(work, "checkout", "-q", "-b", "conflict", "main")
    commit(work, "conflict", **{"a.txt": "theirs\n", "b.txt": "theirs\n"})
    git(work, "push", "-q", "origin", "conflict")
    git(work, "checkout", "-q", "main")
    commit(work, "main moves", **{"a.txt": "ours\n", "b.txt": "ours\n"})
    git(work, "push", "-q", "origin", "main")
    return work


def test_fetch_then_remote_refs_lists_pushed_branches_with_shas(repo: Path, tmp_path: Path) -> None:
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", str(tmp_path / "origin.git"), str(other))
    git(other, "checkout", "-q", "-b", "late", "origin/main")
    late = commit(other, "late", **{"d.txt": "d\n"})
    git(other, "push", "-q", "origin", "late")
    assert "late" not in {ref.branch for ref in ok(git_source.remote_refs(repo))}
    assert git_source.fetch(repo) == Ok(None)
    refs = {ref.branch: ref.sha for ref in ok(git_source.remote_refs(repo))}
    assert set(refs) == {"main", "clean", "conflict", "late"}
    assert refs["late"] == late
    assert refs["main"] == git(repo, "rev-parse", "origin/main")
    assert refs["conflict"] == git(repo, "rev-parse", "origin/conflict")


def test_worktrees_lists_main_and_a_detached_worktree(repo: Path, tmp_path: Path) -> None:
    detached = tmp_path / "detached"
    git(repo, "worktree", "add", "-q", "--detach", str(detached), "origin/clean")
    rows = ok(git_source.worktrees(repo))
    assert [(Path(row.path).resolve(), row.branch, row.head) for row in rows] == [
        (repo.resolve(), "main", git(repo, "rev-parse", "HEAD")),
        (detached.resolve(), None, git(repo, "rev-parse", "origin/clean")),
    ]


def test_clean_branch_merges_clean(repo: Path) -> None:
    merge = ok(git_source.merge(repo, "clean", "main"))
    assert (merge.branch, merge.target, merge.state, merge.files, merge.messages) == ("clean", "main", MergeState.CLEAN, (), ())


def test_conflicting_branch_lists_the_conflicted_files_and_messages(repo: Path) -> None:
    merge = ok(git_source.merge(repo, "conflict", "main"))
    assert merge.state == MergeState.CONFLICT
    assert merge.files == ("a.txt", "b.txt")
    assert merge.messages == (
        "Auto-merging a.txt",
        "CONFLICT (content): Merge conflict in a.txt",
        "Auto-merging b.txt",
        "CONFLICT (content): Merge conflict in b.txt",
    )


@pytest.mark.parametrize(("branch", "target", "missing"), [("nope", "main", "origin/nope"), ("clean", "gone", "origin/gone")])
def test_missing_branch_is_an_error(repo: Path, branch: str, target: str, missing: str) -> None:
    result = git_source.merge(repo, branch, target)
    assert isinstance(result, Err) and isinstance(result.error, NotFound)
    assert result.error.what == missing


def test_bare_repository_record_without_head_is_skipped(repo: Path, tmp_path: Path) -> None:
    origin = tmp_path / "origin.git"
    linked = tmp_path / "linked"
    git(origin, "worktree", "add", "-q", str(linked), "clean")
    listing = git(origin, "worktree", "list", "--porcelain")
    assert listing.splitlines()[:2] == [f"worktree {origin.resolve()}", "bare"]
    result = git_source.worktrees(origin)
    assert isinstance(result, Ok), result
    assert [(Path(row.path).resolve(), row.branch, row.head) for row in result.value] == [
        (linked.resolve(), "clean", git(origin, "rev-parse", "clean")),
    ]
