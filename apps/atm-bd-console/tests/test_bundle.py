from __future__ import annotations

import io
import re
import shutil
import site
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest
from process_helpers import PROCESS_TIMEOUT, build_command, run_process

APP = Path(__file__).resolve().parents[1]
REPO = APP.parents[1]
UV = shutil.which("uv")
pytestmark = pytest.mark.skipif(UV is None, reason="uv is required to build and install the bundle")


def required_uv() -> str:
    assert UV is not None
    return UV


def read_member(archive: tarfile.TarFile, member: tarfile.TarInfo) -> bytes:
    source = archive.extractfile(member)
    assert source is not None
    return source.read()


def run(*args: str, cwd: Path = APP, check: bool = True, text: bool = True):
    if args[:2] == (UV, "build") and cwd.resolve() == APP.resolve():
        pytest.fail("bundle builds require a private temporary checkout, not the shared app", pytrace=False)
    return run_process("bundle test", args, cwd=cwd, check=check, text=text)


@pytest.fixture
def bundle_checkout(tmp_path: Path) -> Path:
    checkout = tmp_path / "bundle-checkout"
    run("git", "clone", "--no-hardlinks", str(REPO), str(checkout), cwd=tmp_path)
    return checkout


def git_install_command(python: Path, commit: str, repo: Path) -> list[str]:
    return [required_uv(), "pip", "install", "--offline", "--no-python-downloads", "--no-build-isolation",
            "--no-deps", "--python", str(python),
            f"git+{repo.as_uri()}@{commit}#subdirectory=apps/atm-bd-console"]


@pytest.mark.parametrize("explicit_cwd", [False, True])
def test_bundle_build_rejects_shared_checkout_before_launch(monkeypatch, explicit_cwd: bool) -> None:
    def unexpected_run(*args, **kwargs):
        pytest.fail("shared-checkout build launched a subprocess")

    monkeypatch.setitem(run.__globals__, "run_process", unexpected_run)
    with pytest.raises(pytest.fail.Exception, match="private temporary checkout"):
        if explicit_cwd:
            run(*build_command("--wheel"), cwd=APP)
        else:
            run(*build_command("--wheel"))


@pytest.mark.parametrize("text", [True, False])
def test_bundle_command_timeout_names_command_and_stderr(monkeypatch, text: bool) -> None:
    def timed_out(argv, **kwargs):
        assert kwargs["timeout"] == PROCESS_TIMEOUT
        assert kwargs["capture_output"] and kwargs["text"] is text
        raise subprocess.TimeoutExpired(argv, PROCESS_TIMEOUT, output=b"partial", stderr=b"stalled index")

    monkeypatch.setattr(subprocess, "run", timed_out)
    with pytest.raises(pytest.fail.Exception, match="git.*archive.*stalled index"):
        run("git", "archive", text=text)


def test_git_install_command_is_offline(tmp_path: Path) -> None:
    command = git_install_command(tmp_path / "python", "abc123", tmp_path)
    assert {"--offline", "--no-python-downloads", "--no-build-isolation", "--no-deps"} <= set(command)
    assert command[-1].startswith("git+file:")
    assert command[-1] == f"git+{tmp_path.as_uri()}@abc123#subdirectory=apps/atm-bd-console"


def tree(path: Path) -> dict[str, bytes]:
    return {p.relative_to(path).as_posix(): p.read_bytes() for p in path.rglob("*") if p.is_file()}


def committed_plugin(commit: str, repo: Path = REPO) -> dict[str, bytes]:
    prefix = "plugins/atm-bd-orchestration/"
    archive = run("git", "archive", "--format=tar", commit, prefix, cwd=repo, text=False).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        return {member.name[len(prefix):]: read_member(source, member)
                for member in source.getmembers() if member.isfile() and member.name.startswith(prefix)}


def contents(archive: Path) -> dict[str, bytes]:
    if archive.suffix == ".whl":
        with zipfile.ZipFile(archive) as wheel:
            return {name: wheel.read(name) for name in wheel.namelist() if not name.endswith("/")}
    with tarfile.open(archive) as sdist:
        return {member.name: read_member(sdist, member) for member in sdist.getmembers() if member.isfile()}


def check_archive(archive: Path, expected: dict[str, bytes], expected_commit: str) -> None:
    files = contents(archive)
    metadata, = (data for name, data in files.items()
                 if (name.endswith(".dist-info/METADATA") if archive.suffix == ".whl"
                     else name.endswith("/PKG-INFO") and name.count("/") == 1))
    assert metadata.splitlines()[0] == b"Metadata-Version: 2.4"
    prefix = "atm_bd_console/" if archive.suffix == ".whl" else next(
        name[: -len("src/atm_bd_console/_build_identity.py")]
        for name in files if name.endswith("src/atm_bd_console/_build_identity.py")
    ) + "src/atm_bd_console/"
    skills_prefix = prefix + "_skills/"
    actual = {name[len(skills_prefix):]: data for name, data in files.items() if name.startswith(skills_prefix)}
    assert actual == expected
    identity = files[prefix + "_build_identity.py"].decode()
    match = re.search(r"^COMMIT = ['\"]([0-9a-f]{40})['\"]$", identity, re.MULTILINE)
    assert match and match.group(1) == expected_commit


def test_wheel_sdist_and_rebuilt_wheel_bundle_every_file(tmp_path: Path, bundle_checkout: Path) -> None:
    app = bundle_checkout / "apps" / "atm-bd-console"
    commit = run("git", "rev-parse", "HEAD", cwd=bundle_checkout).stdout.strip()
    expected = committed_plugin(commit, bundle_checkout)
    assert {"install.py", "registry.yaml", "manifest.yaml", "config/legacy-owned.json"} <= expected.keys()
    direct = tmp_path / "direct"
    source = tmp_path / "source"
    rebuilt = tmp_path / "rebuilt"
    run(*build_command("--wheel", "--out-dir", str(direct)), cwd=app)
    run(*build_command("--sdist", "--out-dir", str(source)), cwd=app)
    sdist = next(source.glob("*.tar.gz"))
    run(*build_command("--wheel", "--out-dir", str(rebuilt), str(sdist)), cwd=app)
    for archive in (next(direct.glob("*.whl")), sdist, next(rebuilt.glob("*.whl"))):
        check_archive(archive, expected, commit)

    # An extracted sdist must not pick up an unrelated plugin beside it.
    foreign = tmp_path / "foreign"
    plugin = foreign / "plugins" / "atm-bd-orchestration"
    plugin.mkdir(parents=True)
    (plugin / "foreign.txt").write_text("not the bundle")
    unpacked = foreign / "apps" / "atm-bd-console"
    unpacked.mkdir(parents=True)
    for name, data in contents(sdist).items():
        destination = unpacked / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    extracted = next(unpacked.iterdir())
    # Place the extracted project where the hook's inferred root is `foreign`.
    moved = foreign / "apps" / "project"
    extracted.rename(moved)
    rebuilt_foreign = tmp_path / "rebuilt-foreign"
    run(*build_command("--wheel", "--out-dir", str(rebuilt_foreign), str(moved)), cwd=moved)
    check_archive(next(rebuilt_foreign.glob("*.whl")), expected, commit)


@pytest.mark.parametrize("commit_value", [None, "short", "g" * 40, "a" * 41, "a" * 40 + "\n"])
def test_sdist_rejects_identity_without_valid_commit(tmp_path: Path, bundle_checkout: Path, commit_value) -> None:
    app = bundle_checkout / "apps" / "atm-bd-console"
    shutil.copy2(APP / "hatch_build.py", app / "hatch_build.py")
    shutil.copy2(APP / "src/atm_bd_console/skill_integrity.py", app / "src/atm_bd_console/skill_integrity.py")
    source = tmp_path / "source"
    run(*build_command("--sdist", "--out-dir", str(source)), cwd=app)
    unpacked = tmp_path / "unpacked"
    for name, data in contents(next(source.glob("*.tar.gz"))).items():
        destination = unpacked / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    package = next(unpacked.iterdir())
    identity = package / "src" / "atm_bd_console" / "_build_identity.py"
    identity_text = "IDENTITY = 'x'\n" + (f"COMMIT = {commit_value!r}\n" if commit_value is not None else "")
    identity.write_text(identity_text)
    dist = tmp_path / "dist"

    result = run(*build_command("--wheel", "--out-dir", str(dist)), cwd=package, check=False)

    assert result.returncode != 0
    assert str(identity.resolve()) in result.stderr and "40-hex COMMIT" in result.stderr
    assert not list(dist.glob("*.whl"))
    assert identity.read_text() == identity_text


def test_git_file_install_bundles_the_plugin(tmp_path: Path, bundle_checkout: Path) -> None:
    commit = run("git", "rev-parse", "HEAD", cwd=bundle_checkout).stdout.strip()
    venv = tmp_path / "venv"
    run(required_uv(), "venv", "--offline", "--no-python-downloads", "--python", sys.executable, str(venv))
    python = venv / "bin" / "python"
    purelib = Path(run(str(python), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))").stdout.strip())
    (purelib / "test-dependencies.pth").write_text("\n".join(site.getsitepackages()) + "\n")
    run(*git_install_command(python, commit, bundle_checkout))
    assert tree(purelib / "atm_bd_console" / "_skills") == committed_plugin(commit, bundle_checkout)


def test_bundle_checks_ignore_stray_plugin_files(tmp_path: Path) -> None:
    clone = tmp_path / "checkout"
    run("git", "clone", "--no-hardlinks", str(REPO), str(clone), cwd=tmp_path)
    test_file = clone / "apps" / "atm-bd-console" / "tests" / "test_bundle.py"
    shutil.copy2(Path(__file__), test_file)
    stray = clone / "plugins" / "atm-bd-orchestration" / "tests" / "__pycache__" / "stray.pyc"
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_bytes(b"ignored sentinel")
    run("git", "check-ignore", str(stray), cwd=clone)
    run(sys.executable, "-m", "pytest", "-q",
        "tests/test_bundle.py::test_wheel_sdist_and_rebuilt_wheel_bundle_every_file",
        "tests/test_bundle.py::test_git_file_install_bundles_the_plugin",
        cwd=clone / "apps" / "atm-bd-console")
    assert stray.read_bytes() == b"ignored sentinel"


def test_missing_plugin_and_bundle_fails_with_paths(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    app = repo / "apps" / "atm-bd-console"
    shutil.copytree(APP, app, ignore=shutil.ignore_patterns(".venv", "__pycache__", "_skills", "_build_identity.py"))
    run("git", "init", cwd=repo)
    run("git", "config", "user.email", "test@example.com", cwd=repo)
    run("git", "config", "user.name", "Test", cwd=repo)
    run("git", "add", ".", cwd=repo)
    run("git", "commit", "-m", "fixture", cwd=repo)
    result = run(*build_command("--wheel", "--out-dir", str(tmp_path / "dist")), cwd=app, check=False)
    assert result.returncode != 0
    assert str(repo / "plugins" / "atm-bd-orchestration") in result.stderr
    assert str(app / "src" / "atm_bd_console" / "_skills") in result.stderr


def test_generated_bundle_is_gitignored() -> None:
    run("git", "check-ignore", "apps/atm-bd-console/src/atm_bd_console/_skills/manifest.yaml", cwd=REPO)
