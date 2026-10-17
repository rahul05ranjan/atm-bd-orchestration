from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict[str, object]) -> None:
        root = Path(self.root).resolve().parents[1]
        output = Path(self.root) / "src" / "atm_bd_console" / "_build_identity.py"
        self._generated_identity = False
        package_version = tomllib.loads((Path(self.root) / "pyproject.toml").read_text())["project"]["version"]
        try:
            toplevel = subprocess.check_output(["git", "-C", root, "rev-parse", "--show-toplevel"], text=True).strip()
            if Path(toplevel).resolve() != root or not (root / "apps" / "atm-bd-console" / "pyproject.toml").is_file():
                raise ValueError("not a console checkout")
            revision = subprocess.check_output(["git", "-C", root, "rev-parse", "--short", "HEAD"], text=True).strip()
            count = subprocess.check_output(["git", "-C", root, "rev-list", "--count", "HEAD"], text=True).strip()
            dirty = subprocess.check_output(["git", "-C", root, "status", "--porcelain"], text=True).strip()
            major, minor, _patch = package_version.split(".", 2)
            identity = f"{major}.{minor}.{count}+g{revision}{'-dirty' if dirty else ''}"
        except (OSError, subprocess.CalledProcessError, ValueError):
            if output.is_file():
                return
            identity = f"{package_version}+unknown"
        output.write_text(f'IDENTITY = {identity!r}\n')
        self._generated_identity = True

    def finalize(self, version: str, build_data: dict[str, object], artifact_path: str) -> None:
        if self._generated_identity:
            (Path(self.root) / "src" / "atm_bd_console" / "_build_identity.py").unlink(missing_ok=True)
