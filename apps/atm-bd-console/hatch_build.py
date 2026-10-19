from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import tomllib
import weakref
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict[str, object]) -> None:
        self._generated_identity = False
        self._manifest_dir: Path | None = None
        self._manifest_finalizer: weakref.finalize | None = None
        try:
            self._initialize(version, build_data)
        except Exception:
            self._cleanup_manifest()
            if self._generated_identity:
                (Path(self.root) / "src" / "atm_bd_console" / "_build_identity.py").unlink(missing_ok=True)
            raise

    def _initialize(self, version: str, build_data: dict[str, object]) -> None:
        package = Path(self.root).resolve()
        root = package.parents[1]
        output = package / "src" / "atm_bd_console" / "_build_identity.py"
        package_version = tomllib.loads((package / "pyproject.toml").read_text())["project"]["version"]
        if (package / "PKG-INFO").is_file() and output.is_file():
            integrity = _load_integrity(package / "src" / "atm_bd_console" / "skill_integrity.py")
            if integrity.read_build_commit(output) is None:
                raise RuntimeError(f"cannot reuse console identity: {output} has no valid 40-hex COMMIT")
            self._include_identity(output, build_data)
            self._include_skills(package, root, build_data, package_version)
            return
        try:
            toplevel = subprocess.check_output(
                ["git", "-C", root, "rev-parse", "--show-toplevel"], text=True, stderr=subprocess.PIPE
            ).strip()
        except FileNotFoundError as error:
            raise RuntimeError("cannot build console identity: git executable is missing") from error
        except (OSError, subprocess.CalledProcessError) as error:
            raise RuntimeError("cannot build console identity: source is not a console checkout") from error
        if Path(toplevel).resolve() != root or not (root / "apps/atm-bd-console" / "pyproject.toml").is_file():
            raise RuntimeError("cannot build console identity: source is not a console checkout")
        try:
            shallow = subprocess.check_output(["git", "-C", root, "rev-parse", "--is-shallow-repository"], text=True).strip()
            if shallow != "false":
                raise RuntimeError("cannot build console identity: checkout is shallow")
            revision = subprocess.check_output(["git", "-C", root, "rev-parse", "--short", "HEAD"], text=True).strip()
            commit = subprocess.check_output(["git", "-C", root, "rev-parse", "HEAD"], text=True).strip()
            count = subprocess.check_output(["git", "-C", root, "rev-list", "--count", "HEAD"], text=True).strip()
            dirty = subprocess.check_output(["git", "-C", root, "status", "--porcelain", "--", ".", ":!.ok"], text=True).strip()
        except (OSError, subprocess.CalledProcessError) as error:
            raise RuntimeError("cannot build console identity: git command failed") from error
        major, minor, _patch = package_version.split(".", 2)
        identity = f"{major}.{minor}.{count}+g{revision}{'-dirty' if dirty else ''}"
        self._include_skills(package, root, build_data, package_version)
        output.write_text(f'IDENTITY = {identity!r}\nCOMMIT = {commit!r}\n')
        self._generated_identity = True
        self._include_identity(output, build_data)

    def _include_identity(self, output: Path, build_data: dict[str, object]) -> None:
        build_data["force_include"][str(output)] = self._dest("_build_identity.py")

    def _dest(self, name: str) -> str:
        prefix = "atm_bd_console" if self.target_name == "wheel" else "src/atm_bd_console"
        return f"{prefix}/{name}"

    def _include_skills(self, package: Path, root: Path, build_data: dict[str, object], package_version: str) -> None:
        plugin = root / "plugins" / "atm-bd-orchestration"
        bundled = package / "src" / "atm_bd_console" / "_skills"
        # An sdist must use its own contents, never a coincidental adjacent checkout.
        source = bundled if (package / "PKG-INFO").is_file() else (plugin if plugin.is_dir() else bundled)
        if not source.is_dir():
            raise RuntimeError(f"cannot bundle skills: neither {plugin} nor {bundled} exists")
        build_data["force_include"][str(source)] = self._dest("_skills")
        self._include_manifest(package, source, build_data, package_version)

    def _include_manifest(self, package: Path, source: Path, build_data: dict[str, object], package_version: str) -> None:
        """Publish skill-manifest.json beside the bundled skills; it exists only in the built artifacts, never in git."""
        integrity = _load_integrity(package / "src" / "atm_bd_console" / "skill_integrity.py")
        self._manifest_dir = Path(tempfile.mkdtemp(prefix="atm-bd-skill-manifest-"))
        self._manifest_finalizer = weakref.finalize(self, shutil.rmtree, self._manifest_dir, ignore_errors=True)
        output = self._manifest_dir / integrity.MANIFEST_NAME
        output.write_text(json.dumps(integrity.build_manifest(source, package_version), indent=2) + "\n", encoding="utf-8")
        build_data["force_include"][str(output)] = self._dest(integrity.MANIFEST_NAME)

    def _cleanup_manifest(self) -> None:
        if self._manifest_finalizer is not None:
            self._manifest_finalizer()
            self._manifest_finalizer = None
        elif self._manifest_dir is not None:
            shutil.rmtree(self._manifest_dir, ignore_errors=True)
        self._manifest_dir = None

    def finalize(self, version: str, build_data: dict[str, object], artifact_path: str) -> None:
        self._cleanup_manifest()
        if self._generated_identity:
            (Path(self.root) / "src" / "atm_bd_console" / "_build_identity.py").unlink(missing_ok=True)


def _load_integrity(path: Path):
    """The console's own manifest code, loaded by path: the build has no installed atm_bd_console to import."""
    spec = importlib.util.spec_from_file_location("_atm_bd_build_skill_integrity", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot build skill manifest: cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module while the class body runs
    spec.loader.exec_module(module)
    return module
