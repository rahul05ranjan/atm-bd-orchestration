from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "atm_bd_console"
# Paths relative to the package root: only these modules may run tools.
SOURCES = frozenset({"atm_source.py", "beads_source.py", "command.py", "git_source.py", "github_source.py", "status.py"})
# Pure modules that must never import a *_source module.
FORBIDDEN_IMPORTERS = frozenset({"model.py", "dispatch.py", "render.py", "prs.py"})
# Only these modules may name the console's on-disk layout; every other module asks paths.py for a path.
PATH_OWNERS = frozenset({"paths.py", "atm_bd.py"})
MAX_LINES = 1000


def _imported_modules(tree: ast.AST) -> list[str]:
    """Every module an Import/ImportFrom names, with `from X import y` also yielding X.y."""
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if base:
                modules.append(base)
            modules.extend(f"{base}.{alias.name}" if base else alias.name for alias in node.names)
    return modules


def _runs_bd_list_all(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and "bd list --all" in node.value:
            return True
        if isinstance(node, (ast.List, ast.Tuple)):
            items = [item.value for item in node.elts if isinstance(item, ast.Constant) and isinstance(item.value, str)]
            if items[:2] == ["bd", "list"] and "--all" in items:
                return True
    return False


def _names_console_layout(tree: ast.AST, modules: list[str]) -> bool:
    """The console directory literal, or an import of the .atm-bd name, outside the path owners."""
    if any(module.split(".")[-1] == "PHASE_DIR" for module in modules):
        return True
    return any(isinstance(node, ast.Constant) and node.value == "atm-bd" for node in ast.walk(tree))


def violations(package: Path) -> list[str]:
    found = []
    for path in sorted(package.rglob("*.py")):
        relative = path.relative_to(package).as_posix()
        text = path.read_text()
        tree = ast.parse(text, filename=str(path))
        modules = _imported_modules(tree)
        if relative not in SOURCES and any(module.split(".")[0] == "subprocess" for module in modules):
            found.append(f"{relative}: subprocess")
        if relative in FORBIDDEN_IMPORTERS and any(module.split(".")[-1].endswith("_source") for module in modules):
            found.append(f"{relative}: source import")
        if relative not in PATH_OWNERS and _names_console_layout(tree, modules):
            found.append(f"{relative}: console layout")
        if len(text.splitlines()) > MAX_LINES:
            found.append(f"{relative}: exceeds {MAX_LINES} lines")
        if _runs_bd_list_all(tree):
            found.append(f"{relative}: bd list --all")
    return found


def test_source_boundaries() -> None:
    assert PACKAGE.is_dir()
    assert violations(PACKAGE) == []


def _plant(root: Path, files: dict[str, str]) -> list[str]:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return violations(root)


def test_planted_source_imports_in_pure_modules_are_flagged(tmp_path: Path) -> None:
    found = _plant(tmp_path, {
        "model.py": "from .beads_source import ready\n",
        "dispatch.py": "from . import atm_source\n",
        "render.py": "import atm_bd_console.git_source as git\n",
        "prs.py": "from atm_bd_console import github_source\n",
    })
    assert sorted(found) == sorted(f"{name}: source import" for name in FORBIDDEN_IMPORTERS)


def test_planted_subprocess_imports_are_flagged(tmp_path: Path) -> None:
    found = _plant(tmp_path, {
        "model.py": "import os, subprocess\n",
        "runner.py": "from subprocess import run\n",
        "nested/atm_source.py": "import subprocess\n",  # allowlist is anchored to the package root
        "atm_source.py": "import subprocess\n",
    })
    assert sorted(found) == ["model.py: subprocess", "nested/atm_source.py: subprocess", "runner.py: subprocess"]


def test_planted_bd_list_all_is_flagged(tmp_path: Path) -> None:
    found = _plant(tmp_path, {
        "beads_source.py": 'ARGV = ["bd", "list", "-l", "phase:bh", "--all", "--json"]\n',
        "status.py": 'COMMAND = "bd list --all --json"\n',
        "atm_source.py": 'ARGV = ["atm", "task", "list", "--all", "--json"]\n',
    })
    assert sorted(found) == ["beads_source.py: bd list --all", "status.py: bd list --all"]


def test_planted_console_layout_outside_the_path_owners_is_flagged(tmp_path: Path) -> None:
    found = _plant(tmp_path, {
        "paths.py": 'CONSOLE_DIR = "atm-bd"\n',
        "config.py": "from .atm_bd import PHASE_DIR\nSTATE = PHASE_DIR\n",
        "log.py": 'NAME = "atm-bd"\n',
        "app.py": 'print("atm-bd-console: lock held")\n',  # a message, not a path
    })
    assert sorted(found) == ["config.py: console layout", "log.py: console layout"]


def test_comments_and_long_files(tmp_path: Path) -> None:
    found = _plant(tmp_path, {
        "model.py": "# reads beads_source output; never import subprocess here\nVALUE = 1\n",
        "render.py": "VALUE = 1\n" + "# line\n" * MAX_LINES,
    })
    assert found == [f"render.py: exceeds {MAX_LINES} lines"]
