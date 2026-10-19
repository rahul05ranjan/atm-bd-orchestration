from __future__ import annotations

import os
import site
import subprocess
import sys
import tomllib
from pathlib import Path
from shutil import copytree, ignore_patterns, which

import pytest
from process_helpers import PROCESS_TIMEOUT, build_command
from process_helpers import run_process as run

from atm_bd_console import __version__, build
from atm_bd_console.build import display_identity, identity

CONSOLE = Path(__file__).resolve().parents[1]
UV = which("uv")


@pytest.mark.parametrize("failure", ["timeout", "exit"])
def test_run_reports_stage_and_captured_diagnostics(monkeypatch, failure: str) -> None:
    calls = []

    def failed_run(argv, **kwargs):
        calls.append((argv, kwargs))
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, PROCESS_TIMEOUT, output=b"partial output", stderr=b"build stalled")
        return subprocess.CompletedProcess(argv, 7, stdout="partial output", stderr="build stalled")

    monkeypatch.setattr(subprocess, "run", failed_run)
    with pytest.raises(pytest.fail.Exception, match="wheel build:.*partial output.*build stalled"):
        run("wheel build", ["uv", "build"])
    assert len(calls) == 1
    assert calls[0][1]["timeout"] == PROCESS_TIMEOUT
    assert calls[0][1]["capture_output"] and calls[0][1]["text"]


def test_run_can_return_an_expected_command_failure(monkeypatch) -> None:
    result = subprocess.CompletedProcess(["uv", "build"], 1, stdout="", stderr="expected refusal")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: result)
    assert run("rejected build", ["uv", "build"], check=False) is result


def test_build_command_is_offline_and_uses_provisioned_interpreter() -> None:
    argv = build_command("--wheel")
    assert "--offline" in argv and "--no-python-downloads" in argv and "--no-build-isolation" in argv
    assert argv[argv.index("--python") + 1] == sys.executable


def git(repo: Path, *args: str) -> None:
    run(f"git {' '.join(args)}", ["git", "-C", str(repo), *args])


def test_display_identity_changes_per_commit_and_marks_dirty_checkouts(tmp_path: Path) -> None:
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
    first = display_identity(repo)
    (repo / "build.txt").write_text("two")
    git(repo, "commit", "-am", "two")
    second = display_identity(repo)
    (repo / "build.txt").write_text("dirty")

    assert first != second
    assert second.startswith(".".join(__version__.split(".")[:2]) + ".") and "+g" in second
    assert display_identity(repo).endswith("-dirty")
    git(repo, "add", "build.txt")
    assert display_identity(repo).endswith("-dirty")
    git(repo, "reset", "--", "build.txt")
    (repo / "untracked.txt").write_text("dirty")
    assert display_identity(repo).endswith("-dirty")


def test_identity_falls_back_for_an_installed_wheel(tmp_path: Path) -> None:
    assert identity(tmp_path) == __version__


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

    assert build.identity() == __version__


@pytest.mark.skipif(which("uv") is None, reason="uv is required to build and install the test wheel")
@pytest.mark.parametrize("dirty", [False, True])
def test_built_wheel_keeps_git_identity_outside_a_repository(tmp_path: Path, dirty: bool) -> None:
    checkout = tmp_path / "checkout"
    copied = checkout / "apps" / "atm-bd-console"
    copytree(CONSOLE, copied, ignore=ignore_patterns(".venv", "__pycache__", "_build_identity.py"))
    copytree(CONSOLE.parents[1] / "plugins" / "atm-bd-orchestration", checkout / "plugins" / "atm-bd-orchestration")
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
    expected_version = tomllib.loads((copied / "pyproject.toml").read_text())["project"]["version"]
    (checkout / ".ok").write_text("")
    assert expected_version == __version__
    dist = tmp_path / "dist"
    run("wheel build", build_command("--wheel", "--out-dir", str(dist)), cwd=copied)
    venv = tmp_path / "outside" / "venv"
    run("create install environment", [UV, "venv", "--offline", "--no-python-downloads", "--python",
                                       sys.executable, str(venv)])
    python = venv / "bin" / "python"
    purelib = Path(run("locate install environment", [str(python), "-c",
                        "import sysconfig; print(sysconfig.get_path('purelib'))"]).stdout.strip())
    (purelib / "test-dependencies.pth").write_text("\n".join(site.getsitepackages()) + "\n")
    install = [UV, "pip", "install", "--offline", "--no-python-downloads", "--no-build-isolation",
               "--no-deps", "--python", str(python)]
    run("wheel install", [*install, str(next(dist.glob("*.whl")))])
    installed = Path(run("locate installed package", [str(python), "-c",
                         "import atm_bd_console; print(atm_bd_console.__file__)"]).stdout.strip())
    assert installed.is_relative_to(purelib)
    assert run("wheel version", [str(venv / "bin" / "atm-bd"), "--version"]).stdout.strip() == expected_version
    run("sdist build", build_command("--sdist", "--out-dir", str(dist)), cwd=copied)
    run("sdist install", [*install, "--reinstall", str(next(dist.glob("*.tar.gz")))])
    assert run("sdist version", [str(venv / "bin" / "atm-bd"), "--version"]).stdout.strip() == expected_version


def test_checkout_display_identity_takes_precedence_over_embedded_identity(tmp_path: Path) -> None:
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
    expected = display_identity(checkout)
    env = {**os.environ, "PYTHONPATH": str(copied / "src")}
    result = run("checkout identity", [sys.executable, "-c",
                 "from atm_bd_console.build import display_identity; print(display_identity())"], env=env, cwd=tmp_path).stdout.strip()
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
        run("shallow clone", ["git", "clone", "--depth", "1", checkout.as_uri(), str(clone)])
        copied = clone / "apps" / "atm-bd-console"
    if failure == "missing_git":
        (copied / "src" / "atm_bd_console" / "_build_identity.py").write_text("IDENTITY = 'stale'\n")
    env = dict(os.environ)
    if failure == "missing_git":
        env["PATH"] = str(tmp_path / "no-git")
    result = run(f"rejected {failure} build", build_command("--wheel", "--out-dir", str(tmp_path / "dist")),
                 cwd=copied, env=env, check=False)
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
    assert identity(tmp_path) == __version__


def test_display_identity_keeps_the_full_form_in_a_checkout() -> None:
    assert identity() == __version__
    assert "+g" in display_identity()
