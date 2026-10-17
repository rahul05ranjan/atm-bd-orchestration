from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from shutil import copytree, ignore_patterns, which

import pytest

from atm_bd_console import __version__, build
from atm_bd_console.build import display_identity, identity

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
    (checkout / ".ok").write_text("")
    if not dirty:
        assert not expected.endswith("-dirty")
    dist = tmp_path / "dist"
    subprocess.run(["uv", "build", "--wheel", "--out-dir", str(dist)], cwd=copied, check=True)
    venv = tmp_path / "outside" / "venv"
    subprocess.run(["uv", "venv", str(venv)], check=True)
    python = venv / "bin" / "python"
    subprocess.run(["uv", "pip", "install", "--python", str(python), str(next(dist.glob("*.whl")))], check=True)
    assert subprocess.check_output([venv / "bin" / "atm-bd-console", "--version"], text=True).strip() == expected
    subprocess.run(["uv", "build", "--sdist", "--out-dir", str(dist)], cwd=copied, check=True)
    subprocess.run(["uv", "pip", "install", "--reinstall", "--no-cache", "--python", str(python),
                    str(next(dist.glob("*.tar.gz")))], check=True)
    assert subprocess.check_output([venv / "bin" / "atm-bd-console", "--version"], text=True).strip() == expected


def test_checkout_identity_takes_precedence_over_embedded_identity(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    copied = checkout / "apps" / "atm-bd-console"
    copytree(CONSOLE, copied, ignore=ignore_patterns(".venv", "__pycache__", "_build_identity.py"))
    (checkout / ".gitignore").write_text("__pycache__/\n")
    git(checkout, "init")
    git(checkout, "config", "user.email", "test@example.com")
    git(checkout, "config", "user.name", "Test")
    git(checkout, "add", ".")
    git(checkout, "commit", "-m", "one")
    (copied / "src" / "atm_bd_console" / "_build_identity.py").write_text("IDENTITY = 'bogus'\n")
    expected = identity(checkout)
    env = {**os.environ, "PYTHONPATH": str(copied / "src")}
    result = subprocess.check_output([sys.executable, "-c", "from atm_bd_console.build import identity; print(identity())"],
                                     env=env, cwd=tmp_path, text=True).strip()
    assert result == expected and result != "bogus" and not result.endswith("-dirty")


@pytest.mark.skipif(which("uv") is None, reason="uv is required to exercise build failures")
@pytest.mark.parametrize("failure", ["export", "missing_git", "shallow"])
def test_build_rejects_sources_without_reliable_git_identity(tmp_path: Path, failure: str) -> None:
    checkout = tmp_path / "checkout"
    copied = checkout / "apps" / "atm-bd-console"
    copytree(CONSOLE, copied, ignore=ignore_patterns(".venv", "__pycache__", "_build_identity.py"))
    (checkout / ".gitignore").write_text("__pycache__/\n")
    if failure != "export":
        git(checkout, "init")
        git(checkout, "config", "user.email", "test@example.com")
        git(checkout, "config", "user.name", "Test")
        git(checkout, "add", ".")
        git(checkout, "commit", "-m", "one")
    if failure == "shallow":
        clone = tmp_path / "shallow"
        subprocess.run(["git", "clone", "--depth", "1", checkout.as_uri(), str(clone)], check=True, capture_output=True)
        copied = clone / "apps" / "atm-bd-console"
    if failure == "missing_git":
        (copied / "src" / "atm_bd_console" / "_build_identity.py").write_text("IDENTITY = 'stale'\n")
    env = dict(os.environ)
    if failure == "missing_git":
        env["PATH"] = str(tmp_path / "no-git")
    result = subprocess.run([which("uv"), "build", "--wheel", "--python", sys.executable,
                             "--out-dir", str(tmp_path / "dist")], cwd=copied, env=env,
                            capture_output=True, text=True, check=False)
    assert result.returncode != 0
    message = {"export": "source is not a console checkout", "missing_git": "git executable is missing",
               "shallow": "checkout is shallow"}[failure]
    assert message in result.stderr


@pytest.mark.parametrize(("embedded", "shown"), [
    ("0.16.490+g846527b", "0.16.490"),            # release install: count only (#254)
    ("0.16.490+g846527b-dirty", "0.16.490+g846527b-dirty"),  # built from a dirty tree: say so
    (None, f"{__version__}+unknown"),
])
def test_display_identity_shortens_only_clean_release_installs(tmp_path: Path, monkeypatch, embedded, shown) -> None:
    monkeypatch.setattr(build, "BUILT_IDENTITY", embedded)
    assert display_identity(tmp_path) == shown
    assert identity(tmp_path) == (embedded or f"{__version__}+unknown")


def test_display_identity_keeps_the_full_form_in_a_checkout() -> None:
    assert display_identity() == identity() and "+g" in display_identity()
