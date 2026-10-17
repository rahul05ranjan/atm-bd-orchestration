from __future__ import annotations

import subprocess
from pathlib import Path
from shutil import copytree, ignore_patterns, which

import pytest

from atm_bd_console import __version__, build
from atm_bd_console.build import identity

CONSOLE = Path(__file__).resolve().parents[1]


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


@pytest.mark.skipif(which("uv") is None, reason="uv is required to build and install the test wheel")
@pytest.mark.parametrize("dirty", [False, True])
def test_built_wheel_keeps_git_identity_outside_a_repository(tmp_path: Path, dirty: bool) -> None:
    checkout = tmp_path / "checkout"
    copied = checkout / "apps" / "atm-bd-console"
    copytree(CONSOLE, copied, ignore=ignore_patterns(".venv", "__pycache__", "_build_identity.py"))
    (checkout / ".gitignore").write_text("__pycache__/\n")
    git(checkout, "init")
    git(checkout, "config", "user.email", "test@example.com")
    git(checkout, "config", "user.name", "Test")
    git(checkout, "add", ".")
    git(checkout, "commit", "-m", "one")
    (copied / "README.md").write_text("two\n")
    git(checkout, "commit", "-am", "two")
    if dirty:
        (copied / "README.md").write_text("dirty\n")
    expected = identity(checkout)
    dist = tmp_path / "dist"
    subprocess.run(["uv", "build", "--wheel", "--out-dir", str(dist)], cwd=copied, check=True)
    venv = tmp_path / "outside" / "venv"
    subprocess.run(["uv", "venv", str(venv)], check=True)
    python = venv / "bin" / "python"
    subprocess.run(["uv", "pip", "install", "--python", str(python), str(next(dist.glob("*.whl")))], check=True)
    assert subprocess.check_output([venv / "bin" / "atm-bd-console", "--version"], text=True).strip() == expected
