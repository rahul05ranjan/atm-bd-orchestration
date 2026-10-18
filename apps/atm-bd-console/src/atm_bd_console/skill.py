"""Install the bundled or released atm-bd-orchestration skill set."""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from . import __version__, skill_source

_INSTALLER_NAME = "_atm_bd_bundled_skill_installer"


def _installer() -> ModuleType:
    path = skill_source.bundled_directory() / "install.py"
    spec = importlib.util.spec_from_file_location(_INSTALLER_NAME, path)
    if spec is None or spec.loader is None:
        raise skill_source.SkillSourceError(f"cannot load bundled installer: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_INSTALLER_NAME] = module
    spec.loader.exec_module(module)
    return module


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="atm-bd skill", description="Install or list atm-bd-orchestration skills.")
    parser.add_argument("version", nargs="?", help="release version such as 0.16.0")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--latest", action="store_true", help="install the head of upstream main")
    source.add_argument("--list", action="store_true", help="list upstream release tags and available sets")
    parser.add_argument("--dest", type=Path, metavar="REPO", help="target repository (default: current directory)")
    parser.add_argument("--orchestration", action="store_true", help="install the orchestration set (the default and only set)")
    parser.add_argument("--codex", action="store_true", help="install skills under .codex")
    parser.add_argument("--set", action="append", default=[], metavar="NAME=VALUE", help="set an installer config value")
    parser.add_argument("--overwrite", action="store_true", help="back up files that differ from the package")
    return parser


def _source_line(name: str, commit: str) -> str:
    return f"installed atm-bd-orchestration orchestration from {name} @ {commit} (atm-bd {__version__})"


def _tree_failure(name: str, error: Exception) -> str:
    """Name the ref only when reading the fetched tree failed; the bundled tree has no ref."""
    if name == "bundled":
        return str(error)
    return f"{name} is not installable by atm-bd {__version__}: {error}"


def run(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.list:
        if args.version or args.dest is not None or args.codex or args.set or args.overwrite:
            parser.error("--list cannot be combined with a version or install options")
        try:
            print("\n".join((*skill_source.list_tags(), "orchestration")))
        except skill_source.SkillSourceError as error:
            print(f"atm-bd skill: {error}", file=sys.stderr)
            return 1
        return 0
    if args.version and args.latest:
        parser.error("a version cannot be combined with --latest")

    try:
        installer = _installer()
        installer.check_tools()
        dest = (args.dest or Path.cwd()).expanduser().resolve()
        if dest.name not in {".claude", ".codex"}:
            dest = dest / (".codex" if args.codex else ".claude")
        overrides = installer.parse_set(args.set)
        with skill_source.resolve_source(version=args.version, latest=args.latest) as source:
            try:
                plan, _config = installer.plan_install(
                    source.pkg_dir, dest, overrides, codex=args.codex or dest.name == ".codex", overwrite=args.overwrite,
                )
                package_version = installer.package_version(source.pkg_dir)
            except (ImportError, KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
                print(f"atm-bd skill: {_tree_failure(source.name, error)}", file=sys.stderr)
                return 1
            if plan.problems:
                raise installer.InstallError("; ".join(plan.problems))
            changed = installer.apply_plan(plan, package_version)
            print(
                f"atm-bd-orchestration {package_version}: {changed} file(s) written, "
                f"{len(plan.writes) - changed} unchanged, {len(plan.removals)} removed, "
                f"{len(plan.backups)} backed up; config and registry updated"
            )
            print(_source_line(source.name, source.commit))
    except skill_source.SkillSourceError as error:
        print(f"atm-bd skill: {error}", file=sys.stderr)
        return 1
    except (ImportError, KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
        print(f"atm-bd skill: {error}", file=sys.stderr)
        return 1
    return 0
