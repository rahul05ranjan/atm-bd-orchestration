from __future__ import annotations

import re
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
REPO = APP.parents[1]
PLUGIN = REPO / "plugins" / "atm-bd-orchestration"
UV = shutil.which("uv")
pytestmark = pytest.mark.skipif(UV is None, reason="uv is required to build and install the bundle")


def run(*args: str, cwd: Path = APP) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=True)


def tree(path: Path) -> dict[str, bytes]:
    return {p.relative_to(path).as_posix(): p.read_bytes() for p in path.rglob("*") if p.is_file()}


def contents(archive: Path) -> dict[str, bytes]:
    if archive.suffix == ".whl":
        with zipfile.ZipFile(archive) as wheel:
            return {name: wheel.read(name) for name in wheel.namelist() if not name.endswith("/")}
    with tarfile.open(archive) as sdist:
        return {member.name: sdist.extractfile(member).read() for member in sdist.getmembers() if member.isfile()}


def check_archive(archive: Path, expected: dict[str, bytes], expected_commit: str) -> None:
    files = contents(archive)
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


def test_wheel_sdist_and_rebuilt_wheel_bundle_every_file(tmp_path: Path) -> None:
    expected = tree(PLUGIN)
    assert {"install.py", "registry.yaml", "manifest.yaml", "config/legacy-owned.json"} <= expected.keys()
    commit = run("git", "rev-parse", "HEAD", cwd=REPO).stdout.strip()
    direct = tmp_path / "direct"
    source = tmp_path / "source"
    rebuilt = tmp_path / "rebuilt"
    run(UV, "build", "--wheel", "--out-dir", str(direct))
    run(UV, "build", "--sdist", "--out-dir", str(source))
    sdist = next(source.glob("*.tar.gz"))
    run(UV, "build", "--wheel", "--out-dir", str(rebuilt), str(sdist))
    for archive in (next(direct.glob("*.whl")), sdist, next(rebuilt.glob("*.whl"))):
        check_archive(archive, expected, commit)


def test_git_file_install_bundles_the_plugin(tmp_path: Path) -> None:
    commit = run("git", "rev-parse", "HEAD", cwd=REPO).stdout.strip()
    venv = tmp_path / "venv"
    run(UV, "venv", str(venv))
    python = venv / "bin" / "python"
    run(UV, "pip", "install", "--python", str(python),
        f"git+{REPO.as_uri()}@{commit}#subdirectory=apps/atm-bd-console")
    site = Path(run(str(python), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))").stdout.strip())
    assert tree(site / "atm_bd_console" / "_skills") == tree(PLUGIN)


def test_missing_plugin_and_bundle_fails_with_paths(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    app = repo / "apps" / "atm-bd-console"
    shutil.copytree(APP, app, ignore=shutil.ignore_patterns(".venv", "__pycache__", "_skills", "_build_identity.py"))
    run("git", "init", cwd=repo)
    run("git", "config", "user.email", "test@example.com", cwd=repo)
    run("git", "config", "user.name", "Test", cwd=repo)
    run("git", "add", ".", cwd=repo)
    run("git", "commit", "-m", "fixture", cwd=repo)
    result = subprocess.run([UV, "build", "--wheel", "--out-dir", str(tmp_path / "dist")], cwd=app,
                            text=True, capture_output=True, check=False)
    assert result.returncode != 0
    assert str(repo / "plugins" / "atm-bd-orchestration") in result.stderr
    assert str(app / "src" / "atm_bd_console" / "_skills") in result.stderr


def test_generated_bundle_is_gitignored() -> None:
    run("git", "check-ignore", "apps/atm-bd-console/src/atm_bd_console/_skills/manifest.yaml", cwd=REPO)
