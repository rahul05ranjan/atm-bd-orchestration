from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict[str, object]) -> None:
        package = Path(self.root).resolve()
        root = package.parents[1]
        output = package / "src" / "atm_bd_console" / "_build_identity.py"
        self._generated_identity = False
        package_version = tomllib.loads((package / "pyproject.toml").read_text())["project"]["version"]
        if (package / "PKG-INFO").is_file() and output.is_file():
            self._include_identity(output, build_data)
            return
        try:
            toplevel = subprocess.check_output(
                ["git", "-C", root, "rev-parse", "--show-toplevel"], text=True, stderr=subprocess.PIPE
            ).strip()
        except FileNotFoundError as error:
            raise RuntimeError("cannot build console identity: git executable is missing") from error
        except (OSError, subprocess.CalledProcessError) as error:
            raise RuntimeError("cannot build console identity: source is not a console checkout") from error
        if Path(toplevel).resolve() != root or not (root / "apps" / "atm-bd-console" / "pyproject.toml").is_file():
            raise RuntimeError("cannot build console identity: source is not a console checkout")
        try:
            shallow = subprocess.check_output(["git", "-C", root, "rev-parse", "--is-shallow-repository"], text=True).strip()
            if shallow != "false":
                raise RuntimeError("cannot build console identity: checkout is shallow")
            revision = subprocess.check_output(["git", "-C", root, "rev-parse", "--short", "HEAD"], text=True).strip()
            count = subprocess.check_output(["git", "-C", root, "rev-list", "--count", "HEAD"], text=True).strip()
            dirty = subprocess.check_output(["git", "-C", root, "status", "--porcelain", "--", ".", ":!.ok"], text=True).strip()
        except (OSError, subprocess.CalledProcessError) as error:
            raise RuntimeError("cannot build console identity: git command failed") from error
        major, minor, _patch = package_version.split(".", 2)
        identity = f"{major}.{minor}.{count}+g{revision}{'-dirty' if dirty else ''}"
        output.write_text(f'IDENTITY = {identity!r}\n')
        self._generated_identity = True
        self._include_identity(output, build_data)

    def _include_identity(self, output: Path, build_data: dict[str, object]) -> None:
        destination = "atm_bd_console/_build_identity.py" if self.target_name == "wheel" else "src/atm_bd_console/_build_identity.py"
        build_data["force_include"][str(output)] = destination

    def finalize(self, version: str, build_data: dict[str, object], artifact_path: str) -> None:
        if self._generated_identity:
            (Path(self.root) / "src" / "atm_bd_console" / "_build_identity.py").unlink(missing_ok=True)
