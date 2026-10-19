"""Install the bundled or released atm-bd-orchestration skill set."""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from . import __version__, skill_integrity, skill_source

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
    parser = argparse.ArgumentParser(
        prog="atm-bd skill",
        description=("Install or list atm-bd-orchestration skills. Bare installs verify the bundled tree "
                     "against the manifest shipped in the same atm-bd wheel."),
    )
    parser.add_argument("version", nargs="?", help="release version such as 0.16.0")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--latest", action="store_true", help="install the latest published release")
    source.add_argument("--list", action="store_true", help="list upstream release tags and available sets")
    parser.add_argument("--dest", type=Path, metavar="REPO", help="target repository (default: current directory)")
    parser.add_argument("--orchestration", action="store_true", help="install the orchestration set (the default and only set)")
    parser.add_argument("--codex", action="store_true", help="install skills under .codex")
    parser.add_argument("--set", action="append", default=[], metavar="NAME=VALUE", help="set an installer config value")
    parser.add_argument("--overwrite", action="store_true", help="back up files that differ from the package")
    return parser


def _source_line(name: str, commit: str) -> str:
    return f"installed atm-bd-orchestration orchestration from {name} @ {commit} (atm-bd {__version__})"


def run(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.list:
        if args.version or args.dest is not None or args.codex or args.set or args.overwrite:
            parser.error("--list cannot be combined with a version or install options")
        try:
            print("\n".join((*skill_source.remote_tags(), "orchestration")))
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
            manifest = skill_integrity.load_manifest(source)
            manifest_version = manifest["version"]
            differing = [result for result in skill_integrity.verify_tree(source.pkg_dir, manifest) if result.status != "ok"]
            if differing:
                for result in differing:
                    print(f"{result.status} {result.path}", file=sys.stderr)
                print(f"atm-bd skill: {source.name} differs from its published {skill_integrity.MANIFEST_NAME} "
                      f"in {len(differing)} file(s); nothing installed", file=sys.stderr)
                return 1
            try:
                plan, _config = installer.plan_install(
                    source.pkg_dir, dest, overrides, codex=args.codex or dest.name == ".codex", overwrite=args.overwrite,
                )
                package_version = installer.package_version(source.pkg_dir)
            except skill_source.INSTALL_ERROR_TYPES as error:
                print(f"atm-bd skill: {skill_source.install_failure_message(source.name, error)}", file=sys.stderr)
                return 1
            if plan.problems:
                raise installer.InstallError("; ".join(plan.problems))
            changed = installer.apply_plan(plan, package_version)
            try:
                skill_integrity.write_install_record(
                    dest, manifest_version, source.commit, manifest,
                    {key: data for key, (data, _executable) in plan.writes.items()}, source=source.name,
                )
            except OSError as error:
                source_args = " --latest" if args.latest else (
                    f" {source.name.removeprefix('v')}" if source.name != "bundled" else ""
                )
                codex_arg = " --codex" if dest.name == ".codex" else ""
                raise skill_source.SkillSourceError(
                    f"files were installed but {skill_integrity.RECORD_NAME} could not be written: {error}; "
                    f"rerun `atm-bd skill{source_args} --dest {dest.parent}{codex_arg} --overwrite` to restore it"
                ) from error
            print(
                f"atm-bd-orchestration {package_version}: {changed} file(s) written, "
                f"{len(plan.writes) - changed} unchanged, {len(plan.removals)} removed, "
                f"{len(plan.backups)} backed up; config and registry updated"
            )
            print(_source_line(source.name, source.commit))
    except (skill_source.SkillSourceError, skill_integrity.IntegrityError) as error:
        print(f"atm-bd skill: {error}", file=sys.stderr)
        return 1
    except skill_source.INSTALL_ERROR_TYPES as error:
        print(f"atm-bd skill: {error}", file=sys.stderr)
        return 1
    return 0
