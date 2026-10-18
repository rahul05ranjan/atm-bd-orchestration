from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tarfile
import tomllib
import xml.etree.ElementTree as ET
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest


# Local fixture commands should finish quickly; bound hangs on every platform.
TEST_COMMAND_TIMEOUT_SECONDS = 30



def write_repo_fixture(
    tmp_path: Path,
    *,
    manifest_wheels: list[str],
    include_crates: bool = True,
    include_python: bool = True,
    python_build_system: str = "maturin",
) -> tuple[Path, Path]:
    workspace = tmp_path / "Cargo.toml"
    workspace.write_text(
        "\n".join(
            [
                "[workspace]",
                'members = ["crates/sc-composer", "crates/sc-compose"]',
                "",
                "[workspace.package]",
                'version = "1.1.0"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    for crate_name in ("sc-composer", "sc-compose"):
        crate_dir = tmp_path / "crates" / crate_name
        crate_dir.mkdir(parents=True)
        dependencies = (
            ['[dependencies]', 'sc-composer = { path = "../sc-composer", version = "1.1.0" }', ""]
            if crate_name == "sc-compose"
            else []
        )
        (crate_dir / "Cargo.toml").write_text(
            "\n".join(
                [
                    "[package]",
                    f'name = "{crate_name}"',
                    'version = "1.1.0"',
                    "",
                    *dependencies,
                ]
            ),
            encoding="utf-8",
        )

    bindings_dir = tmp_path / "bindings" / "python" / "python" / "sc_compose"
    bindings_dir.mkdir(parents=True)
    (bindings_dir / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "bindings" / "python" / "pyproject.toml").write_text(
        "\n".join(
            [
                "[project]",
                'name = "sc-compose"',
                'version = "1.1.0"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "bindings" / "python" / "Cargo.toml").write_text(
        "[package]\nname = \"sc-compose-python\"\nversion = \"1.1.0\"\n",
        encoding="utf-8",
    )

    manifest = tmp_path / "release" / "publish-artifacts.toml"
    manifest.parent.mkdir(parents=True)
    (manifest.parent / "publish-channel-contracts.toml").write_text(
        (repo_root() / "release" / "publish-channel-contracts.toml").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    wheels = ", ".join(f'"{entry}"' for entry in manifest_wheels)
    crates_section = [
        "[[crates]]",
        'artifact = "sc-composer"',
        'package = "sc-composer"',
        'cargo_toml = "crates/sc-composer/Cargo.toml"',
        "publish = true",
        "publish_order = 1",
        "wait_after_publish_seconds = 0",
        "",
        "[[crates]]",
        'artifact = "sc-compose"',
        'package = "sc-compose"',
        'cargo_toml = "crates/sc-compose/Cargo.toml"',
        "publish = true",
        "publish_order = 2",
        "wait_after_publish_seconds = 0",
        "",
    ]
    build_system_lines = {
        "maturin": ['cargo_manifest = "bindings/python/Cargo.toml"'],
        "setuptools": ['build_system = "setuptools"'],
        "unsupported": ['build_system = "flit"'],
        "missing": [],
    }[python_build_system]
    python_section = [
        "[[python_packages]]",
        'artifact = "sc-compose-python"',
        'package = "sc-compose"',
        'manifest = "bindings/python/pyproject.toml"',
        'module = "sc_compose"',
        'publish = "pypi"',
        "",
        "[[python_distributions]]",
        'name = "sc-compose"',
        'source = "bindings/python"',
        *build_system_lines,
        "sdist = true",
        f"wheels = [{wheels}]",
        "",
    ]
    manifest.write_text(
        "\n".join(
            [
                "schema_version = 1",
                "",
                "[project]",
                'name = "fixture"',
                'archive_prefix = "fixture"',
                'description = "Fixture release"',
                'homepage = "https://example.invalid/fixture"',
                'license = "MIT"',
                'readme_dependency_crate = "sc-composer"',
                'renderer_archive_path = "bin/fixture"',
                "",
                "[[release_targets]]",
                'target = "x86_64-unknown-linux-gnu"',
                'os = "ubuntu-latest"',
                'archive = "tar.gz"',
                "",
                "[[release_binaries]]",
                'name = "fixture"',
                "",
                "[[release_binaries]]",
                'name = "fixture-daemon"',
                "",
                *(crates_section if include_crates else []),
                *(python_section if include_python else []),
                "[channels.pypi]",
                'workflow = "pypi-publish.yml"',
                'dispatch_inputs = { target = "production" }',
                'test_repository = "testpypi"',
                'production_repository = "pypi"',
                "",
                "[channels.homebrew]",
                'workflow = "homebrew-publish.yml"',
                'dispatch_inputs = {}',
                'tap_repository = "example/homebrew-tap"',
                'renderer_target = "x86_64-unknown-linux-gnu"',
                "",
                "[[channels.homebrew.formulas]]",
                'path = "Formula/fixture.rb"',
                'template = "release/homebrew/formula.rb.j2"',
                'class = "Fixture"',
                'binaries = ["fixture"]',
                'test_command = "--help"',
                'test_output = "fixture"',
                'release_track = "stable"',
                "",
                "[[channels.homebrew.assets]]",
                'key = "linux"',
                'target = "x86_64-unknown-linux-gnu"',
                "",
                "[channels.winget]",
                'workflow = "winget-publish.yml"',
                'dispatch_inputs = {}',
                'identifier = "example.fixture"',
                'installer_target = "x86_64-unknown-linux-gnu"',
                "",
                "[channels.scoop]",
                'workflow = "scoop-publish.yml"',
                'dispatch_inputs = {}',
                'bucket_repository = "example/scoop-bucket"',
                'manifest_path = "fixture.json"',
                'manifest_template = "release/scoop/manifest.json.j2"',
                'installer_target = "x86_64-unknown-linux-gnu"',
                'binary = "bin/fixture"',
                'renderer_target = "x86_64-unknown-linux-gnu"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    return workspace, manifest


def run_validate_manifest(
    tmp_path: Path, *, manifest_wheels: list[str], **fixture_kwargs: object
) -> subprocess.CompletedProcess[str]:
    workspace, manifest = write_repo_fixture(
        tmp_path,
        manifest_wheels=manifest_wheels,
        **fixture_kwargs,
    )
    return subprocess.run(
        [
            sys.executable,
            str(scripts_root() / "release_artifacts.py"),
            "validate-manifest",
            "--manifest",
            str(manifest),
            "--workspace-toml",
            str(workspace),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
        timeout=TEST_COMMAND_TIMEOUT_SECONDS,
    )


def repo_root() -> Path:
    # Tests are installed at <consumer>/.github/scripts/tests.  Keep this
    # relative to the consumer root so the untouched vendored suite works in
    # every repository that installs the publish kit.
    return Path(__file__).resolve().parents[3]


def scripts_root() -> Path:
    return repo_root() / ".github" / "scripts"


def test_release_artifact_cli_stays_below_the_script_line_ceiling() -> None:
    cli_lines = (scripts_root() / "release_artifacts.py").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(cli_lines) <= 1000
    assert (scripts_root() / "release_manifest.py").is_file()
    assert (scripts_root() / "release_registry.py").is_file()


def test_validate_manifest_accepts_matching_python_release_shape(tmp_path: Path) -> None:
    result = run_validate_manifest(
        tmp_path,
        manifest_wheels=["ubuntu-latest", "macos-latest", "windows-latest"],
    )

    assert result.returncode == 0, result.stderr
    assert "manifest validation passed" in result.stdout


def run_fixture_command(
    tmp_path: Path, *args: str, manifest: Path
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(scripts_root() / "release_artifacts.py"),
            *args,
            "--manifest",
            str(manifest),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
        timeout=TEST_COMMAND_TIMEOUT_SECONDS,
    )


def test_pure_python_manifest_without_crates_loads_and_gates_cargo_legs(tmp_path: Path) -> None:
    result = run_validate_manifest(
        tmp_path, manifest_wheels=["ubuntu-latest"], include_crates=False
    )
    assert result.returncode == 0, result.stderr
    assert "manifest validation passed" in result.stdout

    manifest = tmp_path / "release" / "publish-artifacts.toml"
    plan_result = run_fixture_command(tmp_path, "build-plan", manifest=manifest)
    assert plan_result.returncode == 0, plan_result.stderr
    plan = json.loads(plan_result.stdout)
    assert plan["has_crates"] is False
    assert plan["has_python_wheels"] is True
    assert plan["workspace_toml"] == "Cargo.toml"

    publish_plan = run_fixture_command(tmp_path, "list-publish-plan", manifest=manifest)
    assert publish_plan.returncode == 0, publish_plan.stderr
    assert publish_plan.stdout.strip() == ""


def test_python_matrices_select_the_declared_build_system(tmp_path: Path) -> None:
    maturin_result = run_validate_manifest(tmp_path, manifest_wheels=["ubuntu-latest"])
    assert maturin_result.returncode == 0, maturin_result.stderr
    manifest = tmp_path / "release" / "publish-artifacts.toml"
    wheel_result = run_fixture_command(tmp_path, "python-wheel-matrix", manifest=manifest)
    assert wheel_result.returncode == 0, wheel_result.stderr
    entry = json.loads(wheel_result.stdout)["include"][0]
    assert entry["build_system"] == "maturin"
    assert entry["cargo_manifest"] == "bindings/python/Cargo.toml"

    setuptools_dir = tmp_path / "setuptools"
    setuptools_dir.mkdir()
    setuptools_result = run_validate_manifest(
        setuptools_dir, manifest_wheels=["ubuntu-latest"], python_build_system="setuptools"
    )
    assert setuptools_result.returncode == 0, setuptools_result.stderr
    setuptools_manifest = setuptools_dir / "release" / "publish-artifacts.toml"
    setuptools_wheels = run_fixture_command(
        setuptools_dir, "python-wheel-matrix", manifest=setuptools_manifest
    )
    setuptools_sdists = run_fixture_command(
        setuptools_dir, "python-sdist-matrix", manifest=setuptools_manifest
    )
    assert setuptools_wheels.returncode == 0, setuptools_wheels.stderr
    entry = json.loads(setuptools_wheels.stdout)["include"][0]
    assert entry["build_system"] == "setuptools"
    assert entry["cargo_manifest"] == ""
    assert entry["source"] == "bindings/python"
    assert setuptools_sdists.returncode == 0, setuptools_sdists.stderr
    assert json.loads(setuptools_sdists.stdout)["include"][0]["build_system"] == "setuptools"


def test_validate_manifest_rejects_missing_or_unsupported_build_system(tmp_path: Path) -> None:
    unsupported_dir = tmp_path / "unsupported"
    unsupported_dir.mkdir()
    unsupported = run_validate_manifest(
        unsupported_dir, manifest_wheels=["ubuntu-latest"], python_build_system="unsupported"
    )
    assert unsupported.returncode != 0
    assert "unsupported build_system" in unsupported.stderr

    missing_dir = tmp_path / "missing"
    missing_dir.mkdir()
    missing = run_validate_manifest(
        missing_dir, manifest_wheels=["ubuntu-latest"], python_build_system="missing"
    )
    assert missing.returncode != 0
    assert "unsupported build_system" in missing.stderr


def test_verify_python_release_assets_accepts_manifest_declared_wheels_and_sdist(tmp_path: Path) -> None:
    _, manifest = write_repo_fixture(tmp_path, manifest_wheels=["ubuntu-latest", "windows-latest"])
    assets = tmp_path / "assets"
    assets.mkdir()
    for suffix in ("linux", "windows"):
        with zipfile.ZipFile(assets / f"fixture-{suffix}.whl", "w") as wheel:
            wheel.writestr("fixture-1.1.0.dist-info/METADATA", "Name: sc-compose\nVersion: 1.1.0\n")
    with tarfile.open(assets / "fixture-1.1.0.tar.gz", "w:gz") as sdist:
        metadata = b"Name: sc-compose\nVersion: 1.1.0\n"
        info = tarfile.TarInfo("fixture-1.1.0/PKG-INFO")
        info.size = len(metadata)
        sdist.addfile(info, io.BytesIO(metadata))

    result = subprocess.run(
        [
            sys.executable,
            str(scripts_root() / "release_artifacts.py"),
            "verify-python-release-assets",
            "--manifest",
            str(manifest),
            "--asset-dir",
            str(assets),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
        timeout=TEST_COMMAND_TIMEOUT_SECONDS,
    )

    assert result.returncode == 0, result.stderr
    assert "'sc-compose': {'wheel': 2, 'sdist': 1}" in result.stdout


def test_build_plan_selects_the_manifest_declared_python_upload_tool(tmp_path: Path) -> None:
    maturin_dir = tmp_path / "maturin"
    maturin_dir.mkdir()
    write_repo_fixture(maturin_dir, manifest_wheels=["ubuntu-latest"])
    plan = run_fixture_command(
        maturin_dir, "build-plan", manifest=maturin_dir / "release" / "publish-artifacts.toml"
    )
    assert plan.returncode == 0, plan.stderr
    assert json.loads(plan.stdout)["python_upload_tool"] == "maturin"

    setuptools_dir = tmp_path / "setuptools"
    setuptools_dir.mkdir()
    write_repo_fixture(
        setuptools_dir, manifest_wheels=["ubuntu-latest"], python_build_system="setuptools"
    )
    plan = run_fixture_command(
        setuptools_dir,
        "build-plan",
        manifest=setuptools_dir / "release" / "publish-artifacts.toml",
    )
    assert plan.returncode == 0, plan.stderr
    assert json.loads(plan.stdout)["python_upload_tool"] == "twine"

    rust_only_dir = tmp_path / "rust-only"
    rust_only_dir.mkdir()
    write_repo_fixture(rust_only_dir, manifest_wheels=["ubuntu-latest"], include_python=False)
    plan = run_fixture_command(
        rust_only_dir,
        "build-plan",
        manifest=rust_only_dir / "release" / "publish-artifacts.toml",
    )
    assert plan.returncode == 0, plan.stderr
    assert json.loads(plan.stdout)["python_upload_tool"] == ""


def test_version_resolution_honors_a_pyproject_workspace_toml(tmp_path: Path) -> None:
    """A pure-Python consumer resolves its version without any Cargo.toml."""
    _, manifest = write_repo_fixture(
        tmp_path,
        manifest_wheels=["ubuntu-latest"],
        include_crates=False,
        python_build_system="setuptools",
    )
    version_source = tmp_path / "pyproject.toml"
    version_source.write_text(
        '[project]\nname = "fixture-root"\nversion = "1.1.0"\n', encoding="utf-8"
    )

    verify = run_fixture_command(
        tmp_path,
        "verify-version",
        "--workspace-toml",
        str(version_source),
        "--version",
        "1.1.0",
        manifest=manifest,
    )
    assert verify.returncode == 0, verify.stderr
    assert "version verification passed" in verify.stdout

    lockstep = run_fixture_command(
        tmp_path,
        "verify-version-lockstep",
        "--workspace-toml",
        str(version_source),
        manifest=manifest,
    )
    assert lockstep.returncode == 0, lockstep.stderr

    mismatch = run_fixture_command(
        tmp_path,
        "verify-version",
        "--workspace-toml",
        str(version_source),
        "--version",
        "1.2.0",
        manifest=manifest,
    )
    assert mismatch.returncode != 0
    assert "workspace version mismatch" in mismatch.stderr

    empty_source = tmp_path / "empty.toml"
    empty_source.write_text("", encoding="utf-8")
    unresolved = run_fixture_command(
        tmp_path,
        "verify-version",
        "--workspace-toml",
        str(empty_source),
        "--version",
        "1.1.0",
        manifest=manifest,
    )
    assert unresolved.returncode != 0
    assert "version source must declare" in unresolved.stderr


def test_package_only_release_has_no_binary_asset_expectations(tmp_path):
    workspace, manifest = write_repo_fixture(tmp_path, manifest_wheels=["ubuntu-latest"])
    text = manifest.read_text()
    import re
    text = re.sub(r'\[\[release_binaries\]\]\nname = "[^"]+"\n', '', text)
    text = text.split('[channels.homebrew]', 1)[0]
    manifest.write_text(text)
    result = run_fixture_command(tmp_path, 'validate-manifest', '--workspace-toml', str(workspace), manifest=manifest)
    assert result.returncode == 0, result.stderr
    result = run_fixture_command(tmp_path, 'build-plan', manifest=manifest)
    assert json.loads(result.stdout)['has_release_binaries'] is False
    assert json.loads(result.stdout)['has_python_wheels'] is True
    result = run_fixture_command(tmp_path, 'release-asset-patterns', manifest=manifest)
    assert result.returncode == 0
    assert result.stdout == ''
