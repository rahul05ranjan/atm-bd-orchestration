from __future__ import annotations

import subprocess
from pathlib import Path

from atm_bd_console.build import identity


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def test_identity_changes_per_commit_and_marks_dirty_checkouts(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    (repo / "build.txt").write_text("one")
    git(repo, "add", "build.txt")
    git(repo, "commit", "-m", "one")
    first = identity(repo)
    (repo / "build.txt").write_text("two")
    git(repo, "commit", "-am", "two")
    second = identity(repo)
    (repo / "build.txt").write_text("dirty")

    assert first != second
    assert second.startswith("0.16.") and "+g" in second
    assert identity(repo).endswith("-dirty")


def test_identity_falls_back_for_an_installed_wheel(tmp_path: Path) -> None:
    assert identity(tmp_path) == "0.16.1+unknown"
