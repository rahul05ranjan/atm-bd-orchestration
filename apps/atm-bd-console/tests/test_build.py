from __future__ import annotations

import subprocess
from pathlib import Path
from shutil import copytree

from atm_bd_console import __version__, build
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
    package = repo / "apps" / "atm-bd-console"
    package.mkdir(parents=True)
    (package / "pyproject.toml").write_text("[project]\nname = 'console'\n")
    git(repo, "add", "build.txt", "apps")
    git(repo, "commit", "-m", "one")
    first = identity(repo)
    (repo / "build.txt").write_text("two")
    git(repo, "commit", "-am", "two")
    second = identity(repo)
    (repo / "build.txt").write_text("dirty")

    assert first != second
    assert second.startswith(".".join(__version__.split(".")[:2]) + ".") and "+g" in second
    assert identity(repo).endswith("-dirty")
    git(repo, "add", "build.txt")
    assert identity(repo).endswith("-dirty")
    git(repo, "reset", "--", "build.txt")
    (repo / "untracked.txt").write_text("dirty")
    assert identity(repo).endswith("-dirty")


def test_identity_falls_back_for_an_installed_wheel(tmp_path: Path) -> None:
    assert identity(tmp_path) == f"{__version__}+unknown"


def test_identity_rejects_package_copied_under_a_foreign_git_root(tmp_path: Path, monkeypatch) -> None:
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    git(foreign, "init")
    git(foreign, "config", "user.email", "test@example.com")
    git(foreign, "config", "user.name", "Test")
    (foreign / "foreign.txt").write_text("one")
    git(foreign, "add", "foreign.txt")
    git(foreign, "commit", "-m", "foreign")
    copied = foreign / "apps" / "atm-bd-console" / "src" / "atm_bd_console"
    copytree(Path(build.__file__).resolve().parent, copied)
    monkeypatch.setattr(build, "__file__", str(copied / "build.py"))

    assert build.identity() == f"{__version__}+unknown"
